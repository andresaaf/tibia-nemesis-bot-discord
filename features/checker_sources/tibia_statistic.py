import re
from typing import List, Optional, Tuple, Dict
from .base import CheckerSource
import logging

logger = logging.getLogger(__name__)

ROW_RE = re.compile(r"<tr[^>]*id=\"boss-[^\"]+\"[^>]*>(.*?)</tr>", re.DOTALL | re.IGNORECASE)
NAME_RE = re.compile(r'class=\"boss-name-link\"[^>]*>\s*(.*?)\s*</a>', re.DOTALL | re.IGNORECASE)
NO_CHANCE_RE = re.compile(r'class=\"chance-text[^\"]*\"[^>]*>\s*No\s+Chance\s*<', re.IGNORECASE)
PERCENT_RE = re.compile(r'class=\"chance-percentage[^\"]*\"[^>]*>\s*\((\d{1,3})%\)\s*<', re.IGNORECASE)
LAST_SEEN_DAYS_RE = re.compile(r'(?:Last\s*Seen|Last\s*kill)[^<:]*:\s*(\d{1,4})\s*day', re.IGNORECASE)
# Some pages present a compact relative text like: <span class="days-text">14 days ago</span>
DAYS_TEXT_RE = re.compile(r'class\s*=\s*"days-text"[^>]*>\s*(\d{1,4})\s*day(?:s)?(?:\s+ago)?', re.IGNORECASE)

class TibiaStatisticSource(CheckerSource):
    id = "tibia_statistic"
    display_name = "tibia-statistic.com"

    def world_url(self, world: str) -> str:
        return f"https://www.tibia-statistic.com/bosshunter/details/{world.lower()}"

    async def fetch_world_html(self, world: str) -> Optional[str]:
        url = self.world_url(world)
        try:
            import aiohttp  # type: ignore
            timeout = aiohttp.ClientTimeout(total=15)
            headers = {"User-Agent": "GolluxBot/1.0 (+https://github.com/)"}
            async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
                async with session.get(url) as resp:
                    if resp.status != 200:
                        logger.warning("TibiaStatisticSource.fetch: HTTP %s for %s", resp.status, url)
                        return None
                    return await resp.text()
        except Exception:
            logger.exception("TibiaStatisticSource.fetch: aiohttp request failed")
            return None

    def parse_spawnables(self, html: str) -> List[Tuple[str, Optional[int]]]:
        try:
            split_idx = html.find('id="without-predictions"')
            main_html = html if split_idx == -1 else html[:split_idx]
            unknown_html = "" if split_idx == -1 else html[split_idx:]

            result_map: dict[str, Optional[int]] = {}
            for row in ROW_RE.findall(main_html):
                m_name = NAME_RE.search(row)
                if not m_name:
                    continue
                name = self._clean_html_text(m_name.group(1))
                if NO_CHANCE_RE.search(row):
                    continue
                m_pct = PERCENT_RE.search(row)
                if m_pct:
                    try:
                        pct = int(m_pct.group(1))
                        result_map[name] = pct
                    except Exception:
                        pass

            for row in ROW_RE.findall(unknown_html):
                m_name = NAME_RE.search(row)
                if not m_name:
                    continue
                name = self._clean_html_text(m_name.group(1))
                result_map.setdefault(name, None)

            return [(k, v) for k, v in result_map.items()]
        except Exception:
            logger.exception("TibiaStatisticSource.parse_spawnables: failed to parse html")
            return []

    def _clean_html_text(self, text: str) -> str:
        text = re.sub(r"<[^>]+>", "", text)
        text = re.sub(r"\s+", " ", text)
        return text.strip()

    def parse_days_since_last_kill(self, html: str) -> Dict[str, int]:
        """Best-effort extraction of days since last kill from the details table.
        If the site markup doesn't expose it in a parseable way, returns empty dict.
        """
        out: Dict[str, int] = {}
        try:
            for row in ROW_RE.findall(html):
                m_name = NAME_RE.search(row)
                if not m_name:
                    continue
                name = self._clean_html_text(m_name.group(1))
                # Try multiple patterns to extract numeric days
                m_days = LAST_SEEN_DAYS_RE.search(row)
                if not m_days:
                    m_days = DAYS_TEXT_RE.search(row)
                if m_days:
                    try:
                        out[name] = int(m_days.group(1))
                    except Exception:
                        pass
        except Exception:
            logger.exception("TibiaStatisticSource.parse_days_since_last_kill: failed to parse html")
        return out
