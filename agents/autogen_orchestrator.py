import logging

from config.settings import settings

logger = logging.getLogger(__name__)

try:
    import autogen
    AUTOGEN_AVAILABLE = True
except ImportError:
    autogen = None
    AUTOGEN_AVAILABLE = False
    logger.warning("autogen not installed – AutoGenOrchestrator will be disabled")

# Build LLM config list dynamically from available API keys
llm_configs: list[dict] = []

if settings.openai_api_key:
    llm_configs.append({
        "model": "gpt-4o-mini",
        "api_key": settings.openai_api_key,
    })

if settings.mistral_api_key:
    llm_configs.append({
        "model": "mistral-small-latest",
        "api_key": settings.mistral_api_key,
        "base_url": "https://api.mistral.ai/v1",
        "api_type": "openai",
    })

if settings.google_api_key:
    llm_configs.append({
        "model": "gemini-1.5-flash",
        "api_key": settings.google_api_key,
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "api_type": "openai",
    })


class AutoGenOrchestrator:
    def __init__(self):
        if not AUTOGEN_AVAILABLE or not llm_configs:
            logger.warning(
                "AutoGenOrchestrator disabled: %s",
                "autogen not available" if not AUTOGEN_AVAILABLE else "no LLM API keys configured",
            )
            self.enabled = False
            self.assistant = None
            self.user_proxy = None
            return

        self.enabled = True
        config_list = {"config_list": llm_configs}

        self.assistant = autogen.AssistantAgent(
            name="trading_assistant",
            llm_config=config_list,
        )

        self.user_proxy = autogen.UserProxyAgent(
            name="trading_user_proxy",
            human_input_mode="NEVER",
            max_consecutive_auto_reply=10,
            llm_config=config_list,
        )

    async def run(self, task: str) -> str:
        """Run a task through the AutoGen agent loop.

        Returns an empty string if the orchestrator is disabled.
        """
        if not self.enabled:
            return ""
        try:
            result = await self.user_proxy.a_initiate_chat(
                self.assistant,
                message=task,
            )
            # Return the last message from the conversation
            if result and hasattr(result, "chat_history") and result.chat_history:
                last = result.chat_history[-1]
                return last.get("content", "")
            return str(result) if result else ""
        except Exception as exc:
            logger.error("AutoGenOrchestrator run error: %s", exc)
            return ""


orchestrator = AutoGenOrchestrator()
