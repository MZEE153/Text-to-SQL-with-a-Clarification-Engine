from langchain_google_genai import ChatGoogleGenerativeAI  # LangChain's wrapper around Google's Gemini chat models
from dotenv import load_dotenv  # reads KEY=VALUE lines from .env so GOOGLE_API_KEY becomes available

load_dotenv()  # load .env now, before the Gemini client is created

model = ChatGoogleGenerativeAI(model="gemini-3.6-flash")  # Gemini client (gemini-2.5-flash 404s for new accounts, so 3.6-flash is used)
result = model.invoke("What is the capital of India? Answer in one sentence.")  # one plain chat call: a string in, an AI message object out
print(result.content)  # the reply text (on this model it can arrive as a list of content blocks rather than a plain string)
