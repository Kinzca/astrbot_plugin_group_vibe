import json
import random
import re
import time
from collections import defaultdict, deque
from pathlib import Path

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.message_components import Plain
from astrbot.api.star import Context, Star, StarTools, register

TOPIC_ENDPOINTS = {
    "news60s": "/v2/60s",
    "itnews": "/v2/it-news",
    "ithome": "/v2/it-news/rank",
    "douyin": "/v2/douyin",
    "rednote": "/v2/rednote",
    "bili": "/v2/bili",
    "weibo": "/v2/weibo",
}


@register(
    "astrbot_plugin_group_vibe",
    "Codex",
    "Low-frequency ambient group-chat replies for a QQ test group.",
    "0.1.0",
)
class GroupVibePlugin(Star):
    def __init__(self, context: Context, config: dict | None = None) -> None:
        super().__init__(context)
        self.config = config or {}
        self.history_maxlen = self._get_int("history_maxlen", 14, 4, 50)
        self.message_times_maxlen = self._get_int("message_times_maxlen", 120, 20, 500)
        self.reply_times_maxlen = self._get_int("reply_times_maxlen", 80, 10, 300)

        self.history = defaultdict(lambda: deque(maxlen=self.history_maxlen))
        self.message_times = defaultdict(lambda: deque(maxlen=self.message_times_maxlen))
        self.reply_times = defaultdict(lambda: deque(maxlen=self.reply_times_maxlen))
        self.last_reply_at: dict[str, float] = {}
        self.last_reply_sender: dict[str, str] = {}
        self.last_fact_check_at: dict[str, float] = {}
        self.topic_cache: dict[str, object] = {"expires_at": 0.0, "text": ""}
        self.affection_cache: dict[tuple[str, str], tuple[float, str]] = {}

        self.enabled = self._get_bool("enabled", True)
        self.allowed_group_ids = self._get_keyword_list("allowed_group_ids", "")
        self.quiet_cooldown_seconds = self._get_int("quiet_cooldown_seconds", 60, 0, 3600)
        self.warm_cooldown_seconds = self._get_int("warm_cooldown_seconds", 25, 0, 3600)
        self.hot_cooldown_seconds = self._get_int("hot_cooldown_seconds", 10, 0, 3600)
        self.interaction_window_seconds = self._get_int("interaction_window_seconds", 150, 0, 3600)
        self.interaction_gap_seconds = self._get_int("interaction_gap_seconds", 3, 0, 600)
        self.normal_probability = self._get_float("normal_probability", 0.22, 0.0, 1.0)
        self.question_probability = self._get_float("question_probability", 0.65, 0.0, 1.0)
        self.vibe_probability = self._get_float("vibe_probability", 0.45, 0.0, 1.0)
        self.interaction_probability_floor = self._get_float("interaction_probability_floor", 0.90, 0.0, 1.0)
        self.interaction_probability_cap = self._get_float("interaction_probability_cap", 0.96, 0.0, 1.0)
        self.non_interaction_probability_min = self._get_float("non_interaction_probability_min", 0.03, 0.0, 1.0)
        self.non_interaction_probability_cap = self._get_float("non_interaction_probability_cap", 0.72, 0.0, 1.0)
        self.hot_message_threshold = self._get_int("hot_message_threshold", 8, 1, 100)
        self.warm_message_threshold = self._get_int("warm_message_threshold", 4, 1, 100)
        self.hot_probability_boost = self._get_float("hot_probability_boost", 0.12, 0.0, 1.0)
        self.warm_probability_boost = self._get_float("warm_probability_boost", 0.06, 0.0, 1.0)
        self.heavy_reply_threshold = self._get_int("heavy_reply_threshold", 14, 1, 100)
        self.medium_reply_threshold = self._get_int("medium_reply_threshold", 9, 1, 100)
        self.heavy_reply_multiplier = self._get_float("heavy_reply_multiplier", 0.45, 0.0, 1.0)
        self.medium_reply_multiplier = self._get_float("medium_reply_multiplier", 0.75, 0.0, 1.0)
        self.heavy_reply_cooldown_threshold = self._get_int("heavy_reply_cooldown_threshold", 12, 1, 100)
        self.medium_reply_cooldown_threshold = self._get_int("medium_reply_cooldown_threshold", 8, 1, 100)
        self.heavy_reply_min_cooldown_seconds = self._get_int("heavy_reply_min_cooldown_seconds", 45, 0, 3600)
        self.medium_reply_min_cooldown_seconds = self._get_int("medium_reply_min_cooldown_seconds", 25, 0, 3600)
        self.provider_temperature = self._get_float("provider_temperature", 0.85, 0.0, 2.0)
        self.max_reply_chars = self._get_int("max_reply_chars", 45, 8, 200)
        self.enable_dailyhub_topic_seed = self._get_bool("enable_dailyhub_topic_seed", True)
        self.dailyhub_api_base_url = str(
            self.config.get("dailyhub_api_base_url") or "https://60s.viki.moe"
        ).rstrip("/")
        self.dailyhub_topic_sources = self._get_keyword_list(
            "dailyhub_topic_sources",
            "weibo,bili,douyin,rednote,itnews,ithome,news60s",
        )
        self.dailyhub_topic_refresh_seconds = self._get_int("dailyhub_topic_refresh_seconds", 1800, 60, 86400)
        self.dailyhub_topic_probability = self._get_float("dailyhub_topic_probability", 0.18, 0.0, 1.0)
        self.dailyhub_topic_max_items = self._get_int("dailyhub_topic_max_items", 8, 1, 30)
        self.dailyhub_topic_timeout_seconds = self._get_int("dailyhub_topic_timeout_seconds", 6, 1, 30)
        self.enable_auto_fact_check = self._get_bool("enable_auto_fact_check", True)
        self.fact_check_probability = self._get_float("fact_check_probability", 0.38, 0.0, 1.0)
        self.fact_check_cooldown_seconds = self._get_int("fact_check_cooldown_seconds", 480, 0, 3600)
        self.fact_check_provider_id = str(self.config.get("fact_check_provider_id") or "").strip()
        self.fact_check_max_chars = self._get_int("fact_check_max_chars", 120, 30, 300)
        self.enable_auto_fact_search = self._get_bool("enable_auto_fact_search", True)
        self.fact_search_max_results = self._get_int("fact_search_max_results", 5, 1, 10)
        self.fact_search_timeout_seconds = self._get_int("fact_search_timeout_seconds", 10, 1, 30)
        self.enable_affection_context = self._get_bool("enable_affection_context", True)
        self.affection_data_dir = str(self.config.get("affection_data_dir") or "").strip()
        self.affection_cache_seconds = self._get_int("affection_cache_seconds", 20, 0, 600)

        self.vibe_keywords = self._get_keyword_list(
            "vibe_keywords",
            "笑死,离谱,绷不住,草,好家伙,抽象,乐,逆天,蚌埠住,绝了",
        )
        self.question_keywords = self._get_keyword_list(
            "question_keywords",
            "吗,嘛,么,为什么,咋,怎么,有没有,是不是,啥,？,?",
        )
        self.block_keywords = self._get_keyword_list(
            "block_keywords",
            "借钱,转账,密码,验证码,隐私,地址,身份证,银行卡,表白,分手,吵架,开盒",
        )
        self.continuation_keywords = self._get_keyword_list(
            "continuation_keywords",
            "你,刚才,那,所以,不是,确实,对,哈哈,笑死,然后,但是",
        )
        self.fact_check_keywords = self._get_keyword_list(
            "fact_check_keywords",
            "真的假的,这是真的吗,是真的吗,真吗,有出处吗,来源呢,网传,听说,据说,辟谣,造谣,假消息,靠谱吗,可信么,可信不,真的假的啊",
        )

        logger.info(
            "group_vibe config loaded: "
            f"enabled={self.enabled} "
            f"normal={self.normal_probability:.2f} "
            f"question={self.question_probability:.2f} "
            f"vibe={self.vibe_probability:.2f} "
            f"fact_check={self.enable_auto_fact_check}/{self.fact_check_probability:.2f} "
            f"fact_search={self.enable_auto_fact_search} "
            f"topic_seed={self.enable_dailyhub_topic_seed}/{self.dailyhub_topic_probability:.2f} "
            f"affection_context={self.enable_affection_context} "
            f"cooldowns={self.quiet_cooldown_seconds}/"
            f"{self.warm_cooldown_seconds}/"
            f"{self.hot_cooldown_seconds}"
        )

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    async def ambient_reply(self, event: AstrMessageEvent):
        if not self.enabled:
            return

        group_id = str(getattr(event.message_obj, "group_id", "") or "")
        if self.allowed_group_ids and group_id not in self.allowed_group_ids:
            return

        if event.is_at_or_wake_command:
            return

        sender_id = event.get_sender_id()
        if sender_id and sender_id == event.get_self_id():
            return

        text = (event.get_message_str() or "").strip()
        outline = (event.get_message_outline() or "").strip()
        visible_text = text or outline
        if not visible_text:
            return

        if visible_text.startswith("/"):
            return

        umo = event.unified_msg_origin
        sender_name = event.get_sender_name() or sender_id or "群友"

        latest_text = visible_text
        self.history[umo].append(f"{sender_name}: {latest_text}")
        now = time.time()
        self.message_times[umo].append(now)

        if self._blocked(latest_text):
            return

        if await self._maybe_fact_check(event, umo, latest_text, now):
            return

        is_interaction = self._is_interaction(umo, sender_id, latest_text, now)
        if is_interaction:
            if now - self.last_reply_at.get(umo, 0) < self.interaction_gap_seconds:
                return
        else:
            cooldown = self._dynamic_cooldown(umo, now)
            if now - self.last_reply_at.get(umo, 0) < cooldown:
                return

        probability = self._reply_probability(
            umo,
            latest_text,
            now,
            is_interaction,
        )
        if random.random() >= probability:
            return

        provider = self.context.get_using_provider(umo)
        if not provider:
            logger.warning("group_vibe: no chat provider available")
            return

        try:
            persona = await self.context.persona_manager.get_default_persona_v3(umo)
            persona_prompt = ""
            if isinstance(persona, dict):
                persona_prompt = persona.get("prompt", "") or ""

            topic_hint = await self._maybe_topic_hint()
            affection_hint = self._build_affection_context_hint(event, sender_id)
            response = await provider.text_chat(
                prompt=self._build_prompt(latest_text, list(self.history[umo]), topic_hint),
                session_id=f"group-vibe:{umo}",
                contexts=[],
                system_prompt=self._build_system_prompt(persona_prompt, affection_hint),
                temperature=self.provider_temperature,
            )
            reply = self._clean_reply(response.completion_text)
            if not reply:
                return

            self.last_reply_at[umo] = now
            self.last_reply_sender[umo] = sender_id or ""
            self.reply_times[umo].append(now)
            await event.send(MessageChain([Plain(reply)]))
        except Exception as exc:
            logger.error(f"group_vibe reply failed: {exc}")

    async def _maybe_fact_check(
        self,
        event: AstrMessageEvent,
        umo: str,
        text: str,
        now: float,
    ) -> bool:
        if not self.enable_auto_fact_check:
            return False
        if not self._is_fact_check_candidate(text):
            return False
        if now - self.last_fact_check_at.get(umo, 0) < self.fact_check_cooldown_seconds:
            return False
        if random.random() >= self.fact_check_probability:
            return False

        provider = None
        if self.fact_check_provider_id:
            provider = self.context.get_provider_by_id(self.fact_check_provider_id)
        provider = provider or self.context.get_using_provider(umo)
        if not provider:
            logger.warning("group_vibe: no provider available for fact check")
            return False

        try:
            search_block = await self._fact_search(event, text)
            response = await provider.text_chat(
                prompt=self._build_fact_check_prompt(
                    text,
                    list(self.history[umo]),
                    search_block,
                ),
                session_id=f"group-vibe-fact:{umo}",
                contexts=[],
                system_prompt=(
                    "你是在 QQ 群里顺手帮忙判断消息真假的群友。"
                    "不要像公告或搜索机器人，不要列长清单。"
                    "如果无法确认，就直接说不敢下结论，并提醒需要来源。"
                    "只输出一句自然的群聊回复。"
                ),
                temperature=0.35,
            )
            reply = self._clean_reply(response.completion_text)
            if not reply:
                return False
            if len(reply) > self.fact_check_max_chars:
                reply = reply[: self.fact_check_max_chars].rstrip("，。！？,.!?、 ") + "..."

            self.last_fact_check_at[umo] = now
            self.last_reply_at[umo] = now
            self.last_reply_sender[umo] = event.get_sender_id() or ""
            self.reply_times[umo].append(now)
            await event.send(MessageChain([Plain(reply)]))
            return True
        except Exception as exc:
            logger.error(f"group_vibe fact check failed: {exc}")
            return False

    async def _fact_search(self, event: AstrMessageEvent, text: str) -> str:
        if not self.enable_auto_fact_search:
            return ""
        try:
            tool_manager = getattr(self.context, "get_llm_tool_manager", lambda: None)()
            tool = tool_manager.get_func("anysearch_search") if tool_manager else None
            if not tool or not getattr(tool, "active", True) or not hasattr(tool, "run"):
                logger.debug("group_vibe: anysearch_search tool unavailable")
                return ""
            result = await self._run_with_timeout(
                tool.run(
                    event,
                    query=self._fact_search_query(text),
                    max_results=self.fact_search_max_results,
                    freshness="",
                    content_types=["web", "news"],
                ),
                self.fact_search_timeout_seconds,
            )
            text_result = str(result or "").strip()
            if not text_result or text_result.startswith("错误："):
                logger.warning(f"group_vibe fact search empty/error: {text_result[:120]}")
                return ""
            return text_result[:1800]
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"group_vibe fact search failed: {exc}")
            return ""

    async def _run_with_timeout(self, coro, timeout_seconds: int):
        try:
            import asyncio

            return await asyncio.wait_for(coro, timeout=timeout_seconds)
        except TimeoutError:
            logger.warning("group_vibe fact search timed out")
            return ""

    def _fact_search_query(self, text: str) -> str:
        query = re.sub(r"\s+", " ", text).strip()
        query = re.sub(
            r"(真的假的啊?|这是真的吗|是真的吗|真吗|有出处吗|来源呢|靠谱吗|可信么|可信不)",
            " ",
            query,
        )
        query = re.sub(r"\s+", " ", query).strip()
        return query or text.strip()

    def _is_fact_check_candidate(self, text: str) -> bool:
        lowered = text.lower()
        if any(keyword and keyword in text for keyword in self.fact_check_keywords):
            return True
        if re.search(r"(网传|听说|据说|有人说|真的假的|真假|辟谣|造谣)", text):
            return True
        if ("?" in text or "？" in text) and re.search(
            r"(是不是真的|是不是|靠谱吗|可信|出处|来源|发生了|怎么回事)",
            text,
        ):
            return True
        return any(keyword in lowered for keyword in ("real?", "true?", "fake?"))

    def _build_fact_check_prompt(
        self,
        latest_text: str,
        history: list[str],
        search_block: str = "",
    ) -> str:
        history_text = "\n".join(history[-8:])
        search_text = ""
        if search_block:
            search_text = f"""

联网搜索摘要：
{search_block}

上面的摘要可能不完整，但如果里面有明确来源或时间信息，请优先参考它。
"""
        return f"""
最近群聊：
{history_text}

需要顺手判断的一句：
{latest_text}
{search_text}

请判断这句话里的事实主张是否明显可信。你不一定有联网能力：
- 如果能根据常识或上下文判断，就自然地说一句结论。
- 如果需要最新资料或可靠来源，就说“这个得看来源/我不敢直接下结论”。
- 如果上面给了联网搜索摘要，可以说“看搜到的结果/资料里更像是...”，但不要编造没有出现的来源。
- 不要输出 true/false/unknown 格式。
""".strip()

    def _build_affection_context_hint(self, event: AstrMessageEvent, sender_id: str) -> str:
        if not self.enable_affection_context or not sender_id:
            return ""

        bot_id = self._get_event_self_id(event)
        cache_key = (bot_id or "default_bot", sender_id)
        now = time.time()
        if self.affection_cache_seconds > 0:
            cached = self.affection_cache.get(cache_key)
            if cached and now - cached[0] < self.affection_cache_seconds:
                return cached[1]

        hint = self._read_affection_context_hint(bot_id, sender_id)
        if self.affection_cache_seconds > 0:
            self.affection_cache[cache_key] = (now, hint)
        return hint

    def _read_affection_context_hint(self, bot_id: str, sender_id: str) -> str:
        base_dir = self._affection_base_dir()
        if not base_dir:
            return ""

        for bot_dir in self._affection_candidate_dirs(base_dir, bot_id):
            user_data = self._read_json_file(bot_dir / "user_data.json")
            if not isinstance(user_data, dict):
                continue
            user = user_data.get(sender_id)
            if not isinstance(user, dict):
                continue
            self_data = self._read_json_file(bot_dir / "self_data.json")
            if not isinstance(self_data, dict):
                self_data = {}
            return self._format_affection_context(user, self_data)
        return ""

    def _affection_base_dir(self) -> Path | None:
        if self.affection_data_dir:
            return Path(self.affection_data_dir)
        try:
            return Path(StarTools.get_data_dir("astrbot_plugin_affection"))
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"group_vibe: affection data dir unavailable: {exc}")
            return None

    def _affection_candidate_dirs(self, base_dir: Path, bot_id: str) -> list[Path]:
        candidates: list[Path] = []
        seen: set[str] = set()

        def add(path: Path) -> None:
            key = str(path)
            if key not in seen and path.exists() and path.is_dir():
                candidates.append(path)
                seen.add(key)

        if bot_id:
            add(base_dir / str(bot_id))
        add(base_dir / "default_bot")
        try:
            for child in sorted(base_dir.iterdir(), key=lambda item: item.name):
                add(child)
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"group_vibe: affection data scan skipped: {exc}")
        return candidates

    def _read_json_file(self, path: Path):
        if not path.exists():
            return None
        try:
            with path.open("r", encoding="utf-8") as file:
                return json.load(file)
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"group_vibe: failed to read affection data {path}: {exc}")
            return None

    def _format_affection_context(self, user_data: dict, self_data: dict) -> str:
        affection = self._as_float(user_data.get("affection"), 50.0)
        libido_other = self._as_float(user_data.get("current_libido_other"), 25.0)
        aggression_other = self._as_float(user_data.get("current_aggression_other"), 25.0)
        libido_self = self._as_float(self_data.get("current_libido_self"), 25.0)
        aggression_self = self._as_float(self_data.get("current_aggression_self"), 25.0)

        if affection >= 85:
            relation = "已经很熟，语气可以更放松，偶尔接梗或开一点小玩笑"
        elif affection >= 70:
            relation = "熟人偏亲近，可以自然热络一点，但别突然太黏"
        elif affection >= 55:
            relation = "友好普通群友，顺着话题接一句就好"
        elif affection >= 40:
            relation = "还不算很熟，保持自然礼貌，别强行亲近"
        else:
            relation = "距离感更强，少主动贴近，回复要短一点"

        if libido_other >= 37.5 and aggression_other < 25:
            edge = "可以更愿意回应对方，语气稍微暖一点"
        elif aggression_other >= 37.5:
            edge = "可以有一点别扭或轻微吐槽，但不要攻击、挑衅或冷暴力"
        elif libido_other <= 12.5:
            edge = "兴致偏低，像普通群友随口搭一句，不要表现得过度热情"
        else:
            edge = "关系状态中性，主要看当前群聊上下文"

        if aggression_self >= 37.5:
            self_tone = "自身状态偏紧绷，最多体现为一句短促或没精神，不要卖惨"
        elif libido_self >= 37.5:
            self_tone = "自身状态比较稳定，可以更自信、更轻松一点"
        else:
            self_tone = "自身状态一般，保持平常聊天感"

        return f"""
当前关系氛围（内部参考，不要说出来）：
- 对刚才发言的群友：{relation}
- 临场边界：{edge}
- 自身语气：{self_tone}
使用要求：这只是语气微调，不要提好感度、插件、情绪数值或关系面板；即使状态很极端，也只转成普通群聊里的轻微熟稔、别扭、吐槽或保持距离。
""".strip()

    def _get_event_self_id(self, event: AstrMessageEvent) -> str:
        try:
            bot_id = event.get_self_id()
            if bot_id:
                return str(bot_id)
        except Exception:  # noqa: BLE001
            pass
        message_obj = getattr(event, "message_obj", None)
        bot_id = getattr(message_obj, "self_id", None)
        return str(bot_id) if bot_id else "default_bot"

    @staticmethod
    def _as_float(value, default: float) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    async def _maybe_topic_hint(self) -> str:
        if not self.enable_dailyhub_topic_seed:
            return ""
        if random.random() >= self.dailyhub_topic_probability:
            return ""

        now = time.time()
        cached = str(self.topic_cache.get("text") or "")
        if cached and now < float(self.topic_cache.get("expires_at") or 0):
            return cached

        text = await self._fetch_topic_seed()
        if text:
            self.topic_cache = {
                "expires_at": now + self.dailyhub_topic_refresh_seconds,
                "text": text,
            }
        return text

    async def _fetch_topic_seed(self) -> str:
        try:
            import aiohttp
        except Exception:  # noqa: BLE001
            logger.warning("group_vibe: aiohttp unavailable, skip topic seed")
            return ""

        timeout = aiohttp.ClientTimeout(total=self.dailyhub_topic_timeout_seconds)
        collected: list[str] = []
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                for key in self.dailyhub_topic_sources:
                    endpoint = TOPIC_ENDPOINTS.get(key)
                    if not endpoint:
                        continue
                    try:
                        async with session.get(f"{self.dailyhub_api_base_url}{endpoint}") as resp:
                            if resp.status != 200:
                                continue
                            payload = await resp.text()
                            data = json.loads(payload)
                            collected.extend(self._extract_topic_titles(key, data))
                    except Exception as exc:  # noqa: BLE001
                        logger.debug(f"group_vibe topic source skipped: {key} {exc}")
                    if len(collected) >= self.dailyhub_topic_max_items:
                        break
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"group_vibe topic seed fetch failed: {exc}")
            return ""

        titles = []
        seen = set()
        for item in collected:
            clean = re.sub(r"\s+", " ", str(item)).strip()
            if clean and clean not in seen:
                titles.append(clean)
                seen.add(clean)
            if len(titles) >= self.dailyhub_topic_max_items:
                break
        if not titles:
            return ""
        return "\n".join(f"- {title}" for title in titles)

    def _extract_topic_titles(self, key: str, payload: dict) -> list[str]:
        data = payload.get("data") if isinstance(payload, dict) else payload
        items = self._find_topic_items(data)
        titles = []
        for item in items:
            if isinstance(item, str):
                titles.append(f"{key}: {item}")
                continue
            if not isinstance(item, dict):
                continue
            title = (
                item.get("title")
                or item.get("name")
                or item.get("word")
                or item.get("keyword")
                or item.get("content")
                or item.get("desc")
            )
            if title:
                titles.append(f"{key}: {title}")
        return titles

    def _find_topic_items(self, data) -> list:
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            for key in ("items", "list", "news", "hot", "data", "rank", "results"):
                value = data.get(key)
                if isinstance(value, list):
                    return value
            for value in data.values():
                if isinstance(value, list):
                    return value
        return []

    def _reply_probability(
        self,
        umo: str,
        text: str,
        now: float,
        is_interaction: bool,
    ) -> float:
        if any(keyword in text for keyword in self.question_keywords):
            probability = self.question_probability
        elif any(keyword in text for keyword in self.vibe_keywords):
            probability = self.vibe_probability
        else:
            probability = self.normal_probability

        if is_interaction:
            return min(
                self.interaction_probability_cap,
                max(probability, self.interaction_probability_floor),
            )

        recent_messages = self._recent_count(self.message_times[umo], now, 90)
        if recent_messages >= self.hot_message_threshold:
            probability += self.hot_probability_boost
        elif recent_messages >= self.warm_message_threshold:
            probability += self.warm_probability_boost

        recent_replies = self._recent_count(self.reply_times[umo], now, 600)
        if recent_replies >= max(self.heavy_reply_threshold, recent_messages * 2):
            probability *= self.heavy_reply_multiplier
        elif recent_replies >= max(self.medium_reply_threshold, recent_messages):
            probability *= self.medium_reply_multiplier

        return max(self.non_interaction_probability_min, min(self.non_interaction_probability_cap, probability))

    def _blocked(self, text: str) -> bool:
        return any(keyword in text for keyword in self.block_keywords)

    def _dynamic_cooldown(self, umo: str, now: float) -> int:
        recent_messages = self._recent_count(self.message_times[umo], now, 90)
        recent_replies = self._recent_count(self.reply_times[umo], now, 600)
        if recent_messages >= self.hot_message_threshold:
            cooldown = self.hot_cooldown_seconds
        elif recent_messages >= self.warm_message_threshold:
            cooldown = self.warm_cooldown_seconds
        else:
            cooldown = self.quiet_cooldown_seconds

        if recent_replies >= self.heavy_reply_cooldown_threshold:
            cooldown = max(cooldown, self.heavy_reply_min_cooldown_seconds)
        elif recent_replies >= self.medium_reply_cooldown_threshold:
            cooldown = max(cooldown, self.medium_reply_min_cooldown_seconds)
        return cooldown

    def _is_interaction(
        self,
        umo: str,
        sender_id: str,
        text: str,
        now: float,
    ) -> bool:
        last_reply_at = self.last_reply_at.get(umo, 0)
        if now - last_reply_at > self.interaction_window_seconds:
            return False
        if sender_id and sender_id == self.last_reply_sender.get(umo):
            return True
        if any(keyword in text for keyword in self.question_keywords):
            return True
        return any(keyword in text for keyword in self.continuation_keywords)

    def _recent_count(self, values: deque[float], now: float, seconds: int) -> int:
        while values and now - values[0] > seconds:
            values.popleft()
        return len(values)

    def _build_system_prompt(self, persona_prompt: str, affection_hint: str = "") -> str:
        affection_block = f"\n\n{affection_hint}" if affection_hint else ""
        return f"""
{persona_prompt}
{affection_block}

你现在是在 QQ 群里偶尔接话的普通群友小号。
回复要像群聊插话，不像客服、助手、主持人或总结机器人。
不要复述和总结群友刚说过的话，不要说“我可以帮你”，不要主动解释规则。
多数时候只回一句，最长 35 个中文字符。
如果正在有人接你的话，可以自然继续聊；如果只是路过插话，要短一点。
可以接梗、轻微吐槽、顺着话题聊，但不要强行热场。
不要连续追问，不要每次都用同一种口癖。
被问到身份、是不是 AI、是不是机器人时，要如实说是实验小号。
涉及借钱、隐私、账号、承诺、吵架、感情决定、违法内容时不要替任何人表态。
只输出要发到群里的那句话，不要输出引号、前缀或解释。
""".strip()

    def _build_prompt(self, latest_text: str, history: list[str], topic_hint: str = "") -> str:
        history_text = "\n".join(history[-10:])
        topic_block = ""
        if topic_hint:
            topic_block = f"""

可用的今日话题素材：
{topic_hint}

这些只是给你找话题用的素材。只有和群聊当前话题贴合时才顺手提一句，不要播报新闻，不要逐条总结。
"""
        return f"""
最近群聊：
{history_text}

最新一句：
{latest_text}
{topic_block}

判断你现在是否适合自然接一句。适合的话，只写一句像普通群友会发的话。
""".strip()

    def _clean_reply(self, text: str) -> str:
        text = (text or "").strip()
        text = re.sub(r"^['\"“”‘’]+|['\"“”‘’]+$", "", text).strip()
        text = re.sub(r"^(回复|群友回复|我会说|可以回)[:：]\s*", "", text).strip()
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        text = lines[0] if lines else ""
        if len(text) > self.max_reply_chars:
            text = text[:self.max_reply_chars].rstrip("，。！？,.!?、 ") + "..."
        return text

    def _get_bool(self, key: str, default: bool) -> bool:
        value = self.config.get(key, default)
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "on", "开启", "是")
        return bool(value)

    def _get_int(self, key: str, default: int, min_value: int, max_value: int) -> int:
        try:
            value = int(self.config.get(key, default))
        except (TypeError, ValueError):
            value = default
        return max(min_value, min(max_value, value))

    def _get_float(self, key: str, default: float, min_value: float, max_value: float) -> float:
        try:
            value = float(self.config.get(key, default))
        except (TypeError, ValueError):
            value = default
        return max(min_value, min(max_value, value))

    def _get_keyword_list(self, key: str, default: str) -> tuple[str, ...]:
        value = self.config.get(key, default)
        if value is None:
            return ()
        if isinstance(value, (list, tuple, set)):
            parts = value
        else:
            parts = str(value).replace("，", ",").replace("\n", ",").split(",")
        return tuple(str(part).strip() for part in parts if str(part).strip())
