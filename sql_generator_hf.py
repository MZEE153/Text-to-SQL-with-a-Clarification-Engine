"""Step 5, Hugging Face variant: same job as sql_generator.py (given a
resolved question, produce a SQLGenerationResult), but backed by a free HF
Inference Providers model instead of Gemini -- built to route around
Gemini's free-tier daily cap (20 requests/day for gemini-3.6-flash, hit
during Step 6 integration testing).

Reuses the same Pydantic schema and system prompt as sql_generator.py --
only the generation mechanism differs.

Confirmed finding: ChatHuggingFace.with_structured_output(SQLGenerationResult)
raises NotImplementedError("Pydantic schema is not supported for function
calling") -- HuggingFaceEndpoint(task="text-generation") has no tool-calling
support to hang structured output off of, unlike Gemini/Groq. This was a
flagged risk in the project setup notes, now confirmed rather than assumed.

Fallback: PydanticOutputParser instead of with_structured_output -- inject
the schema as prompt text (get_format_instructions()), ask the model to
return matching JSON, parse the raw text response ourselves. This is the
pre-tool-calling LangChain pattern, and it's the right fallback specifically
because it needs nothing from the provider except "can follow instructions
and emit text" -- no function-calling support required.
"""
from dotenv import load_dotenv
from langchain_core.output_parsers import PydanticOutputParser
from langchain_huggingface import ChatHuggingFace, HuggingFaceEndpoint

from sql_generator import SYSTEM_PROMPT, SQLGenerationResult
from schema_retrieval import retrieve_relevant_tables

load_dotenv()

llm = HuggingFaceEndpoint(
    repo_id="Qwen/Qwen2.5-7B-Instruct",
    task="text-generation",
    max_new_tokens=512,
    temperature=0.1,
)
model = ChatHuggingFace(llm=llm)

parser = PydanticOutputParser(pydantic_object=SQLGenerationResult)
FORMAT_INSTRUCTIONS = parser.get_format_instructions()


def generate_sql(resolved_question: str) -> SQLGenerationResult:
    relevant = retrieve_relevant_tables(resolved_question, top_k=4)
    schema_text = "\n\n".join(desc for _, desc, _ in relevant)
    system = SYSTEM_PROMPT.format(schema=schema_text) + "\n\n" + FORMAT_INSTRUCTIONS
    response = model.invoke([("system", system), ("human", resolved_question)])
    return parser.parse(response.content)


def print_result(resolved_question: str) -> None:
    print(f"Resolved question: {resolved_question}")
    result = generate_sql(resolved_question)
    print(f"  tables_used: {result.tables_used}")
    print(f"  explanation: {result.explanation}")
    print(f"  sql:\n{result.sql}")
    print()


if __name__ == "__main__":
    test_questions = [
        "How many systems signed off last year?",
        "Who was the best customer last year, defined as highest revenue?",
        "Who was the best customer last year, defined as highest total profit?",
        "What is the total sales in all years? Assume completed orders only, summed across all time.",
    ]
    for q in test_questions:
        print_result(q)
