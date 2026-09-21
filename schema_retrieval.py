"""Step 3b: embed each table's schema description, then retrieve the
top-K most relevant tables for a given natural-language question --
RAG over the schema, same mechanic as the PDF embedding-search lab,
applied to table descriptions instead of document chunks.
"""
from langchain_huggingface import HuggingFaceEmbeddings  # LangChain wrapper that runs a Hugging Face embedding model locally
from sklearn.metrics.pairwise import cosine_similarity  # scores how similar two vectors are (1 = same direction, 0 = unrelated)

from schema_introspection import inspector, describe_table  # Step 3a: the live-database inspector and the table -> plain-English describer

embedding = HuggingFaceEmbeddings(model_name="sentence-transformers/all-MiniLM-L6-v2")  # small local model: text -> 384-number vector; downloaded once, then cached

table_names = inspector.get_table_names()  # every table in the database (customers, orders, order_items, systems)
table_descriptions = [describe_table(name) for name in table_names]  # plain-English description of each table, same order as table_names
table_embeddings = embedding.embed_documents(table_descriptions)  # one vector per description, computed once at import time and reused for every question


def retrieve_relevant_tables(question: str, top_k: int = 2) -> list[str]:  # rank tables by relevance to a question and return the best top_k
    # Note: the "-> list[str]" hint is inaccurate -- the real return value is a list of (name, description, score) tuples.
    """Return the top_k table descriptions most relevant to the question."""
    query_embedding = embedding.embed_query(question)  # turn the question into a vector using the same model as the tables
    scores = cosine_similarity([query_embedding], table_embeddings)[0]  # similarity of the question to every table (one score per table)
    ranked = sorted(zip(table_names, table_descriptions, scores), key=lambda x: x[2], reverse=True)  # bundle (name, description, score), sort best score first
    return ranked[:top_k]  # keep only the top_k best matches


if __name__ == "__main__":  # only runs when executed directly, not when imported
    test_questions = [  # sample questions to eyeball the retrieval quality
        "How many systems signed off last year?",  # should rank the systems table first
        "Who was the best customer last year?",  # should rank customers and orders (near-tied)
        "What products have the highest profit margin?",  # should rank order_items (it holds product_name and cost_price)
    ]

    for question in test_questions:  # run retrieval for each question
        print(f"Query: {question}")  # show the question
        for name, desc, score in retrieve_relevant_tables(question, top_k=2):  # top 2 tables with their scores
            print(f"  -> {name} (score: {score:.4f})")  # table name and its similarity score to 4 decimal places
        print()  # blank line between questions
