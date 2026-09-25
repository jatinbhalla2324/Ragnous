"""Single-PDF ingest.

This script used to embed with Cohere embed-multilingual-v3.0 (1024-d) while
scripts/bulk_ingest.py, scripts/math_ingest.py and the query path in
app/api/v1/chat.py all used paraphrase-multilingual-MiniLM-L12-v2 (384-d) —
and all four read from or wrote to the same ncert_chunks.embedding column.
Rows written by this script could therefore never be retrieved: at best the
dimensions clash and Postgres rejects the insert, and if the column had been
widened to 1024 instead, every *query* would have failed instead. It now uses
the same embedding_service as everything else. Cohere is still used in the
retrieval path, but as a cross-encoder reranker, not an embedder.
"""

import os
import sys
import asyncio
import argparse
from pathlib import Path
from dotenv import load_dotenv

# Repo root on the path first, so this runs from any working directory.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import asyncpg  # noqa: E402
from langchain_community.document_loaders import PyPDFLoader  # noqa: E402
from langchain_text_splitters import RecursiveCharacterTextSplitter  # noqa: E402

from scripts.pdf_limits import configure_pdf_limits  # noqa: E402

# Without this, a chapter with a large compressed stream raises inside
# PyPDFLoader and never reaches the index.
configure_pdf_limits()

from app.services.embedding_service import (  # noqa: E402
    EMBEDDING_DIM,
    EMBEDDING_MODEL_NAME,
    embed_texts,
    to_pgvector,
    verify_embedding_dim,
)

# Load environment variables
load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

# Clean asyncpg URL if it has +asyncpg
if DATABASE_URL and DATABASE_URL.startswith("postgresql+asyncpg://"):
    DATABASE_URL = DATABASE_URL.replace("+asyncpg", "")

async def ingest_pdf(file_path: str, subject: str, chapter: str):
    if not DATABASE_URL:
        print("Error: DATABASE_URL is not set in .env")
        return
        
    print(f"Loading {file_path}...")
    loader = PyPDFLoader(file_path)
    pages = loader.load()
    print(f"Loaded {len(pages)} pages.")

    # Split text into manageable chunks
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=1000,
        chunk_overlap=200,
        length_function=len,
    )
    chunks = text_splitter.split_documents(pages)
    print(f"Split into {len(chunks)} chunks.")

    # Match bulk_ingest.py: prepend the book/chapter/page header so the vector
    # encodes provenance, and so the same passage produces the same text under
    # either script. Without this the two scripts stored the same chunk in two
    # different forms and the query-time dedup had to strip the header to spot
    # the duplicate.
    contextual_contents = []
    for chunk in chunks:
        page_num = chunk.metadata.get("page", 0) + 1
        header = f"[Book: {subject} | Chapter: {chapter} | Page: {page_num}]\n"
        contextual_contents.append((page_num, header + chunk.page_content))

    verify_embedding_dim()
    print(f"Generating embeddings locally with {EMBEDDING_MODEL_NAME} ({EMBEDDING_DIM}-d)...")
    try:
        embeddings = embed_texts([c for _, c in contextual_contents])
    except Exception as e:
        print(f"Error generating embeddings: {e}")
        return

    print("Connecting to database...")
    try:
        conn = await asyncpg.connect(DATABASE_URL)
        
        # Insert chunks into ncert_chunks table
        print(f"Inserting {len(contextual_contents)} chunks into the database...")
        inserted = 0
        first_error = None
        for i, ((page_number, content), embedding) in enumerate(
            zip(contextual_contents, embeddings)
        ):
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
                if first_error is None:
                    first_error = e

            if (i+1) % 50 == 0:
                print(f"Inserted {inserted}/{i+1} chunks...")

        await conn.close()

        # Report what actually landed. Claiming success after inserting nothing
        # is how a whole book could go missing without anyone noticing.
        if inserted < len(contextual_contents):
            print(
                f"❌ Only {inserted}/{len(contextual_contents)} chunks were stored. "
                f"First error: {first_error}"
            )
            print(
                f"   If this is a dimension error, ncert_chunks.embedding must be "
                f"VECTOR({EMBEDDING_DIM}) to match {EMBEDDING_MODEL_NAME}."
            )
            sys.exit(1)

        print(f"Ingestion completed successfully — {inserted} chunks stored.")
        
    except Exception as e:
        print(f"Database error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ingest a PDF book into the database.")
    parser.add_argument("file_path", help="Path to the PDF file")
    parser.add_argument("--subject", default="General", help="Subject of the book")
    parser.add_argument("--chapter", default="Full Book", help="Chapter name")
    
    args = parser.parse_args()
    
    if not os.path.exists(args.file_path):
        print(f"Error: File not found at {args.file_path}")
        exit(1)
        
    asyncio.run(ingest_pdf(args.file_path, args.subject, args.chapter))
