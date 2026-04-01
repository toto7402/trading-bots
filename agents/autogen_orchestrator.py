import logging

from config.settings import settings

logger = logging.getLogger(__name__)

try:
    from autogen_agentchat.agents import AssistantAgent
    from autogen_agentchat.teams import RoundRobinGroupChat
    from autogen_ext.models.openai import OpenAIChatCompletionClient
    from autogen_core.models import ModelInfo
    AUTOGEN_AVAILABLE = True
except ImportError:
    AUTOGEN_AVAILABLE = False
    logger.warning("autogen_agentchat not installed - AutoGenOrchestrator will be disabled")


def _build_client():
    """Build first available LLM client.

    Priority: Mistral → Claude (Anthropic) → OpenAI → Gemini
    Uses OpenAI-compatible endpoint for each provider.
    """
    if not AUTOGEN_AVAILABLE:
        return None

    # Mistral via OpenAI-compatible API
    if settings.mistral_api_key:
        try:
            client = OpenAIChatCompletionClient(
                model="mistral-small-latest",
                api_key=settings.mistral_api_key,
                base_url="https://api.mistral.ai/v1",
                model_info=ModelInfo(
                    vision=False,
                    function_calling=True,
                    json_output=True,
                    family="mistral",
                    structured_output=False,
                ),
            )
            logger.info("AutoGen LLM: Mistral (mistral-small-latest)")
            return client
        except Exception as exc:
            logger.warning("Mistral client failed: %s", exc)

    # Claude (Anthropic) via OpenAI-compatible endpoint
    if settings.anthropic_api_key:
        try:
            client = OpenAIChatCompletionClient(
                model="claude-sonnet-4-6",
                api_key=settings.anthropic_api_key,
                base_url="https://api.anthropic.com/v1/",
                model_info=ModelInfo(
                    vision=True,
                    function_calling=True,
                    json_output=True,
                    family="claude",
                    structured_output=False,
                ),
            )
            logger.info("AutoGen LLM: Claude (claude-sonnet-4-6)")
            return client
        except Exception as exc:
            logger.warning("Claude client failed: %s", exc)

    # OpenAI
    if settings.openai_api_key:
        try:
            client = OpenAIChatCompletionClient(
                model="gpt-4o-mini",
                api_key=settings.openai_api_key,
            )
            logger.info("AutoGen LLM: OpenAI (gpt-4o-mini)")
            return client
        except Exception as exc:
            logger.warning("OpenAI client failed: %s", exc)

    # Gemini via OpenAI-compatible API
    if settings.google_api_key:
        try:
            client = OpenAIChatCompletionClient(
                model="gemini-1.5-flash",
                api_key=settings.google_api_key,
                base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
                model_info=ModelInfo(
                    vision=True,
                    function_calling=True,
                    json_output=True,
                    family="gemini",
                    structured_output=False,
                ),
            )
            logger.info("AutoGen LLM: Gemini (gemini-1.5-flash)")
            return client
        except Exception as exc:
            logger.warning("Gemini client failed: %s", exc)

    return None


class AutoGenOrchestrator:
    """Multi-agent orchestrator using AutoGen 0.4+ (autogen-agentchat).

    Agents:
      - market_analyst:   identifies trends and market conditions
      - risk_manager:     evaluates position sizing and risk limits
      - signal_generator: produces BUY/SELL/HOLD signals with confidence
      - executor:         converts approved signals to order instructions
    """

    def __init__(self):
        has_any_key = bool(
            settings.mistral_api_key
            or settings.anthropic_api_key
            or settings.openai_api_key
            or settings.google_api_key
        )
        if not AUTOGEN_AVAILABLE or not has_any_key:
            logger.warning(
                "AutoGenOrchestrator disabled: %s",
                "autogen_agentchat not available" if not AUTOGEN_AVAILABLE
                else "no LLM API keys configured",
            )
            self.enabled = False
            self._agents: list = []
            return

        client = _build_client()
        if client is None:
            logger.warning("AutoGenOrchestrator disabled: could not build any LLM client")
            self.enabled = False
            self._agents = []
            return

        self.enabled = True

        self._market_analyst = AssistantAgent(
            name="market_analyst",
            model_client=client,
            system_message=(
                "You are a market analyst. Analyse market conditions, identify trends, "
                "and provide data-driven observations about asset prices and macro factors."
            ),
        )

        self._risk_manager = AssistantAgent(
            name="risk_manager",
            model_client=client,
            system_message=(
                "You are a risk manager. Evaluate position sizing, drawdown risk, "
                "concentration risk, and ensure all trades comply with risk limits. "
                "Max 2% risk per trade, max 10% portfolio drawdown."
            ),
        )

        self._signal_generator = AssistantAgent(
            name="signal_generator",
            model_client=client,
            system_message=(
                "You are a trading signal generator. Based on market analysis, "
                "generate BUY/SELL/HOLD signals with confidence scores (0-100) and rationale. "
                "Format: SIGNAL:<BUY|SELL|HOLD> TICKER:<symbol> CONFIDENCE:<0-100> REASON:<text>"
            ),
        )

        self._executor = AssistantAgent(
            name="executor",
            model_client=client,
            system_message=(
                "You are the trade executor. Review approved signals, confirm risk approval, "
                "and provide execution instructions: order type (LIMIT/MARKET), "
                "quantity (shares), and timing (immediate/open/close)."
            ),
        )

        self._agents = [
            self._market_analyst,
            self._risk_manager,
            self._signal_generator,
            self._executor,
        ]
        logger.info(
            "AutoGenOrchestrator ready: %d agents, Mistral=%s Claude=%s OpenAI=%s Gemini=%s",
            len(self._agents),
            bool(settings.mistral_api_key),
            bool(settings.anthropic_api_key),
            bool(settings.openai_api_key),
            bool(settings.google_api_key),
        )

    async def run(self, task: str) -> str:
        """Run a trading analysis task through the multi-agent group chat."""
        if not self.enabled or not self._agents:
            return ""
        try:
            team = RoundRobinGroupChat(
                self._agents,
                max_turns=len(self._agents),
            )
            result = await team.run(task=task)
            if result and hasattr(result, "messages") and result.messages:
                last = result.messages[-1]
                return getattr(last, "content", str(last))
            return str(result) if result else ""
        except Exception as exc:
            logger.error("AutoGenOrchestrator.run error: %s", exc)
            return ""


orchestrator = AutoGenOrchestrator()
