"""LangChain agent that uses CP tools."""
from config import settings
from agent.tools import get_tools
from utils.logging import get_logger

logger = get_logger(__name__)

SYSTEM_PROMPT = """You are a Competitive Programming assistant. You help the user:
- Check upcoming contests on Codeforces and LeetCode
- Register for contests when they ask
- Summarize practice (problems solved, weak/strong tags)
- Suggest training plans and send email reminders

Use the tools to fetch data and perform actions. Be concise. When the user says they want to register for a contest, use register_for_contest_tool with the correct platform and contest id/slug."""


def create_agent():
    if not settings.OPENAI_API_KEY:
        logger.warning("OPENAI_API_KEY not set; agent will not run with LLM")
        return None
    try:
        from langchain.agents import AgentExecutor, create_tool_calling_agent
        from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
        from langchain_openai import ChatOpenAI
    except ImportError as e:
        logger.warning("LangChain agent imports failed: %s", e)
        return None
    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0, api_key=settings.OPENAI_API_KEY)
    tools = get_tools()
    prompt = ChatPromptTemplate.from_messages([
        ("system", SYSTEM_PROMPT),
        MessagesPlaceholder(variable_name="chat_history", optional=True),
        ("human", "{input}"),
        MessagesPlaceholder(variable_name="agent_scratchpad"),
    ])
    agent = create_tool_calling_agent(llm, tools, prompt)
    return AgentExecutor(agent=agent, tools=tools, verbose=True, handle_parsing_errors=True)
