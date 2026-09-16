"""Step 3b: embed each table's schema description, then retrieve the
top-K most relevant tables for a given natural-language question --
RAG over the schema, same mechanic as the PDF embedding-search lab,
applied to table descriptions instead of document chunks.
"""
from langchain_huggingface import HuggingFaceEmbeddings
from sklearn.metrics.pairwise import cosine_similarity

from schema_introspection import inspector, describe_table

embedding = HuggingFaceEmbeddings(model_name="sentence-transformers/all-MiniLM-L6-v2")

table_names = inspector.get_table_names()
table_descriptions = [describe_table(name) for name in table_names]
table_embeddings = embedding.embed_documents(table_descriptions)


def retrieve_relevant_tables(question: str, top_k: int = 2) -> list[str]:
    """Return the top_k table descriptions most relevant to the question."""
    query_embedding = embedding.embed_query(question)
    scores = cosine_similarity([query_embedding], table_embeddings)[0]
    ranked = sorted(zip(table_names, table_descriptions, scores), key=lambda x: x[2], reverse=True)
    return ranked[:top_k]


if __name__ == "__main__":
    test_questions = [
        "How many systems signed off last year?",
        "Who was the best customer last year?",
        "What products have the highest profit margin?",
    ]

    for question in test_questions:
        print(f"Query: {question}")
        for name, desc, score in retrieve_relevant_tables(question, top_k=2):
            print(f"  -> {name} (score: {score:.4f})")
        print()
