import logging

from config.settings import settings

logger = logging.getLogger(__name__)

try:
    from google import genai
    from google.genai import types as genai_types
    GEMINI_AVAILABLE = True
except ImportError:
    try:
        import google.generativeai as genai
        genai_types = None
        GEMINI_AVAILABLE = True
        logger.warning("Using deprecated google.generativeai - please upgrade to google-genai")
    except ImportError:
        genai = None
        genai_types = None
        GEMINI_AVAILABLE = False


class AISupervisor:
    """Gemini-powered AI supervisor using the google-genai SDK."""

    def __init__(self):
        if not settings.google_api_key or not GEMINI_AVAILABLE:
            if not settings.google_api_key:
                logger.warning("Gemini disabled: GOOGLE_API_KEY not set")
            elif not GEMINI_AVAILABLE:
                logger.warning("Gemini disabled: google-genai not installed")
            self.gemini = None
            self._use_new_sdk = False
        else:
            try:
                # Try new google-genai SDK first
                if genai_types is not None:
                    self._client = genai.Client(api_key=settings.google_api_key)
                    self.gemini = "gemini-2.0-flash"
                    self._use_new_sdk = True
                    logger.info("Gemini initialised (google-genai SDK, model=%s)", self.gemini)
                else:
                    # Fall back to deprecated SDK
                    genai.configure(api_key=settings.google_api_key)
                    self.gemini = genai.GenerativeModel("gemini-1.5-flash")
                    self._use_new_sdk = False
                    logger.info("Gemini initialised (google.generativeai SDK)")
            except Exception as exc:
                logger.warning("Gemini initialisation failed: %s", exc)
                self.gemini = None
                self._use_new_sdk = False

    async def analyze(self, prompt: str) -> str:
        """Send a prompt to Gemini and return the response text."""
        if self.gemini is None:
            return ""
        try:
            if self._use_new_sdk:
                response = await self._client.aio.models.generate_content(
                    model=self.gemini,
                    contents=prompt,
                )
                return response.text or ""
            else:
                response = await self.gemini.generate_content_async(prompt)
                return response.text or ""
        except Exception as exc:
            logger.error("Gemini analyze error: %s", exc)
            return ""

    def is_gemini_active(self) -> bool:
        return self.gemini is not None


supervisor = AISupervisor()
