# inspect_db.py
import chromadb

# Connect to the same ChromaDB used in RAG_Pipeline.py
chroma_client = chromadb.PersistentClient(path="chroma_db")
collection = chroma_client.get_or_create_collection(name="file_embeddings")

def inspect_collection():
    print("📊 Inspecting ChromaDB collection...\n")

    # Get count
    try:
        total_count = collection.count()
        print(f"✅ Total vectors stored: {total_count}")
    except Exception as e:
        print(f"⚠️ Could not get count: {e}")
        total_count = 0

    if total_count == 0:
        print("🚫 No vectors found in the DB.")
        return

    # Peek into a few documents
    try:
        sample = collection.get(limit=5)
        print("\n🔎 Sample entries:")
        for i, doc in enumerate(sample.get("documents", [])):
            meta = sample.get("metadatas", [{}])[i]
            print(f"   - ID: {sample['ids'][i]}")
            print(f"     Source: {meta.get('source')}")
            print(f"     Text: {doc[:100]}...\n")
    except Exception as e:
        print(f"⚠️ Could not fetch sample: {e}")

    # Group by file name (source)
    try:
        all_entries = collection.get(include=["metadatas"])
        sources = [m.get("source") for m in all_entries.get("metadatas", []) if m]
        from collections import Counter
        counts = Counter(sources)
        print("📂 Chunks per file:")
        for file, count in counts.items():
            print(f"   {file}: {count} chunks")
    except Exception as e:
        print(f"⚠️ Could not group by source: {e}")

if __name__ == "__main__":
    inspect_collection()
