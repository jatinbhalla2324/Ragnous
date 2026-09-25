import os
import sys
import asyncio
import glob
import zipfile
import shutil
from pathlib import Path
from dotenv import load_dotenv

# Repo root on the path first, so this runs from any working directory.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import asyncpg  # noqa: E402
from langchain_community.document_loaders import PyPDFLoader  # noqa: E402
from langchain_text_splitters import RecursiveCharacterTextSplitter  # noqa: E402

from scripts.pdf_limits import configure_pdf_limits  # noqa: E402

# Must run before any PyPDFLoader call, or chapters with large compressed
# streams are dropped without an error.
configure_pdf_limits()

from app.services.embedding_service import (  # noqa: E402
    EMBEDDING_DIM,
    EMBEDDING_MODEL_NAME,
    get_embedding_model,
    to_pgvector,
    verify_embedding_dim,
)

# Load environment variables
load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

if DATABASE_URL and DATABASE_URL.startswith("postgresql+asyncpg://"):
    DATABASE_URL = DATABASE_URL.replace("+asyncpg", "")

# The model and its dimension come from embedding_service so this script and
# the query path in app/api/v1/chat.py cannot drift onto different models.
print("⏳ Loading local embedding model...")
print("   (Multilingual: 50+ languages including Hindi and Punjabi. Runs 100% locally!)")
model = get_embedding_model()
verify_embedding_dim()
print(f"✅ {EMBEDDING_MODEL_NAME} loaded ({EMBEDDING_DIM}-d).")

# Every skipped chapter and every rejected chunk is recorded here. Previously
# a chapter that failed to load, or a chunk the database rejected, was printed
# and stepped over — and the run still ended with "All files processed and
# embedded successfully!". A dimension mismatch could reject 100% of inserts
# and the script would still report success.
FAILURES: list = []


async def process_pdf(conn, file_path: str, subject: str, chapter: str):
    print(f"  -> Processing {Path(file_path).name} (Subject: {subject}, Chapter: {chapter})")
    
    loader = PyPDFLoader(file_path)
    try:
        pages = loader.load()
    except Exception as e:
        print(f"     Error loading {file_path}: {e}")
        FAILURES.append((chapter, f"load failed: {e}"))
        return

    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=1000,
        chunk_overlap=200,
        length_function=len,
    )
    chunks = text_splitter.split_documents(pages)
    if not chunks:
        print("     No text found.")
        FAILURES.append((chapter, "no extractable text (scanned/image-only PDF?)"))
        return

    # Prepend Book and Chapter context headers to every chunk text so the vector embedding model
    # encodes the exact Book Title, Subject, and Chapter directly into the vector space.
    contextual_contents = []
    for chunk in chunks:
        page_num = chunk.metadata.get("page", 0) + 1
        header = f"[Book: {subject} | Chapter: {chapter} | Page: {page_num}]\n"
        contextual_contents.append((page_num, header + chunk.page_content))
    
    texts_to_embed = [item[1] for item in contextual_contents]
    
    try:
        # Generate embeddings locally (no API rate limits!)
        embeddings = model.encode(texts_to_embed).tolist()
    except Exception as e:
        print(f"     Error generating embeddings locally: {e}")
        FAILURES.append((chapter, f"embedding failed: {e}"))
        return

    inserted = 0
    for i, ((page_number, content), embedding) in enumerate(zip(contextual_contents, embeddings)):
        embedding_str = to_pgvector(embedding)
        
        try:
            await conn.execute(
                """
                INSERT INTO ncert_chunks (subject, chapter, page_number, content, embedding)
                VALUES ($1, $2, $3, $4, $5::vector)
                """,
                subject, chapter, page_number, content, embedding_str
            )
            inserted += 1
        except Exception as e:
            print(f"     Failed to insert chunk {i}: {e}")
            # One line per rejected chunk would bury the summary; the first is
            # enough to diagnose (they fail for the same reason — almost always
            # a VECTOR(n) mismatch against EMBEDDING_DIM).
            if inserted == 0 and i == 0:
                FAILURES.append((chapter, f"insert rejected: {e}"))

    if inserted < len(contextual_contents):
        print(f"     ⚠️  inserted {inserted}/{len(contextual_contents)} chunks")
    else:
        print(f"     inserted {inserted} chunks")

async def process_zip(conn, zip_path: str):
    zip_filename = Path(zip_path).stem
    print(f"\n=== Unzipping and Processing Archive: {zip_filename}.zip ===")
    
    # Normalize subject name from zip filename
    subject = zip_filename.strip()
    temp_dir = os.path.join(Path(zip_path).parent, f"temp_{zip_filename}")
    
    with zipfile.ZipFile(zip_path, 'r') as zip_ref:
        zip_ref.extractall(temp_dir)
        
    pdf_files = glob.glob(os.path.join(temp_dir, "**/*.pdf"), recursive=True)
    print(f"Found {len(pdf_files)} PDFs inside {zip_filename}.zip")
    
    for pdf_path in pdf_files:
        chapter = Path(pdf_path).stem
        await process_pdf(conn, pdf_path, subject=subject, chapter=chapter)
        
    shutil.rmtree(temp_dir)
    print(f"=== Finished Archive: {zip_filename}.zip ===\n")


async def bulk_ingest(directory: str):
    if not DATABASE_URL:
        print("Error: DATABASE_URL is missing in .env")
        return
        
    zip_files = glob.glob(os.path.join(directory, "**/*.zip"), recursive=True)
    pdf_files = [f for f in glob.glob(os.path.join(directory, "**/*.pdf"), recursive=True) if "temp_" not in f]
    
    if not zip_files and not pdf_files:
        print(f"No .zip or .pdf files found in {directory}")
        return
        
    print(f"Found {len(zip_files)} ZIP files and {len(pdf_files)} loose PDF files under '{directory}'.")
    
    try:
        conn = await asyncpg.connect(DATABASE_URL)
        
        # Process ZIPs
        for zip_path in zip_files:
            await process_zip(conn, zip_path)
            
        # Process loose PDFs if any
        for pdf_path in pdf_files:
            filename = Path(pdf_path).stem
            parts = filename.split('_')
            subject = parts[0] if len(parts) > 0 else "Unknown"
            chapter = parts[1] if len(parts) > 1 else "Unknown"
            print(f"\n=== Processing Loose PDF: {filename}.pdf ===")
            await process_pdf(conn, pdf_path, subject, chapter)
            
        await conn.close()

        if FAILURES:
            print(f"\n❌ {len(FAILURES)} chapter(s) did NOT make it into the index:")
            for chapter, why in FAILURES:
                print(f"   - {chapter}: {why}")
            print("\nThe index is incomplete. Fix the above and re-run before "
                  "trusting retrieval — a missing chapter is invisible at query "
                  "time; it just never gets retrieved.")
            sys.exit(1)

        print("\n🎉 All files processed and embedded successfully with full book & chapter context!")
    except Exception as e:
        print(f"Database connection error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    directory = "data/books"
    if not os.path.exists(directory):
        print(f"Directory {directory} does not exist.")
    else:
        asyncio.run(bulk_ingest(directory))

