import logging

from config.settings import settings

logger = logging.getLogger(__name__)

try:
    import google.generativeai as genai
    GEMINI_AVAILABLE = True
except ImportError:
    genai = None
    GEMINI_AVAILABLE = False


class AISupervisor:
    def __init__(self):
        if not settings.google_api_key or not GEMINI_AVAILABLE:
            logger.warning("Gemini disabled: GOOGLE_API_KEY not set")
            self.gemini = None
        else:
            try:
                genai.configure(api_key=settings.google_api_key)
                self.gemini = genai.GenerativeModel("gemini-1.5-flash")
            except Exception as exc:
                logger.warning("Gemini initialisation failed: %s", exc)
                self.gemini = None

    async def analyze(self, prompt: str) -> str:
        """Send a prompt to Gemini and return the response text.

        Returns an empty string if Gemini is not available.
        """
        if self.gemini is None:
            return ""
        try:
            response = await self.gemini.generate_content_async(prompt)
            return response.text
        except Exception as exc:
            logger.error("Gemini analyze error: %s", exc)
            return ""

    def is_gemini_active(self) -> bool:
        """Return True only when Gemini is configured and ready."""
        return self.gemini is not None


supervisor = AISupervisor()
