import os
import sys
import asyncio
import glob
import zipfile
import shutil
import nest_asyncio
from pathlib import Path
from dotenv import load_dotenv

import asyncpg
from llama_parse import LlamaParse
from langchain_text_splitters import RecursiveCharacterTextSplitter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services.embedding_service import (  # noqa: E402
    EMBEDDING_DIM,
    EMBEDDING_MODEL_NAME,
    get_embedding_model,
    to_pgvector,
    verify_embedding_dim,
)

# LlamaParse uses asyncio under the hood, so we need nest_asyncio when running within an event loop
nest_asyncio.apply()

# Load environment variables
load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
if DATABASE_URL and DATABASE_URL.startswith("postgresql+asyncpg://"):
    DATABASE_URL = DATABASE_URL.replace("+asyncpg", "")

LLAMAPARSE_API_KEY = os.getenv("LLAMAPARSE_API_KEY")

# Model and dimension come from embedding_service — see the note there on why
# naming a model directly in each script is what broke retrieval.
print("⏳ Loading local embedding model...")
print("   (Multilingual: 50+ languages including Hindi and Punjabi. Runs 100% locally!)")
model = get_embedding_model()
verify_embedding_dim()
print(f"✅ {EMBEDDING_MODEL_NAME} loaded ({EMBEDDING_DIM}-d).")

print("Initializing LlamaParse...")
# Initialize LlamaParse
# We instruct it to parse mathematically complex documents and keep the markdown format.
parser = LlamaParse(
    api_key=LLAMAPARSE_API_KEY,
    result_type="markdown",
    verbose=True,
    # Instruction helps it handle Hindi, Punjabi, and LaTeX math formulas specifically
    system_prompt="This is a textbook containing Hindi, Punjabi, English and complex mathematical formulas. Please extract all formulas exactly in LaTeX/markdown format, and preserve the multilingual text accurately."
)
print("✅ LlamaParse initialized successfully!")

async def process_pdf(conn, file_path: str, subject: str, chapter: str):
    print(f"  -> Parsing {Path(file_path).name} with LlamaParse (Subject: {subject}, Chapter: {chapter})")
    
    try:
        # LlamaParse sends the file to their API which uses Vision models to extract perfectly structured markdown
        documents = await parser.aload_data(file_path)
    except Exception as e:
        print(f"     Error parsing {file_path} with LlamaParse: {e}")
        return

    full_text = "\n\n".join([doc.text for doc in documents])
    
    if not full_text.strip():
        print("     No text extracted. The PDF might be empty or unreadable.")
        return
        
    print(f"     Extracted {len(full_text)} characters of perfect Markdown text.")

    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=1000,
        chunk_overlap=200,
        length_function=len,
    )
    
    chunks = text_splitter.split_text(full_text)
    print(f"     Split into {len(chunks)} chunks.")
    
    try:
        # Generate embeddings locally
        embeddings = model.encode(chunks).tolist()
    except Exception as e:
        print(f"     Error generating embeddings: {e}")
        return

    for i, (chunk_text, embedding) in enumerate(zip(chunks, embeddings)):
        embedding_str = to_pgvector(embedding)
        
        try:
            await conn.execute(
                """
                INSERT INTO ncert_chunks (subject, chapter, page_number, content, embedding)
                VALUES ($1, $2, $3, $4, $5::vector)
                """,
                subject, chapter, 0, chunk_text, embedding_str  # using 0 for page_number as a placeholder
            )
        except Exception as e:
            print(f"     Failed to insert chunk {i}: {e}")

async def process_zip(conn, zip_path: str):
    zip_filename = Path(zip_path).stem
    print(f"\n=== Unzipping and Parsing Math Archive: {zip_filename}.zip ===")
    
    subject = zip_filename 
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


async def math_ingest(directory: str):
    if not DATABASE_URL or not LLAMAPARSE_API_KEY:
        print("Error: DATABASE_URL or LLAMAPARSE_API_KEY is missing in .env")
        return
        
    zip_files = glob.glob(os.path.join(directory, "*.zip"))
    pdf_files = glob.glob(os.path.join(directory, "*.pdf"))
    
    if not zip_files and not pdf_files:
        print(f"No .zip or .pdf files found in {directory}")
        return
        
    print(f"Starting Math-specific LlamaParse Bulk Ingestion...")
    print(f"Found {len(zip_files)} ZIP files and {len(pdf_files)} loose PDF files.")
    
    try:
        conn = await asyncpg.connect(DATABASE_URL)
        
        for zip_path in zip_files:
            await process_zip(conn, zip_path)
            
        for pdf_path in pdf_files:
            filename = Path(pdf_path).stem
            parts = filename.split('_')
            subject = parts[0] if len(parts) > 0 else "Unknown"
            chapter = parts[1] if len(parts) > 1 else "Unknown"
            print(f"\n=== Processing Loose PDF: {filename}.pdf ===")
            await process_pdf(conn, pdf_path, subject, chapter)
            
        await conn.close()
        print("\n🎉 All math files parsed, extracted, embedded and stored successfully!")
    except Exception as e:
        print(f"Database connection error: {e}")

if __name__ == "__main__":
    directory = "data/math_books"
    if not os.path.exists(directory):
        print(f"Directory {directory} does not exist. Creating it now.")
        os.makedirs(directory)
    else:
        asyncio.run(math_ingest(directory))
