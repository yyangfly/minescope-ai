from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from hashlib import sha1

from pipeline.models import SearchHit
from pipeline.text import expand_query
from pipeline.vector_store import SQLiteVectorStore
from serve.llm import OpenAICompatibleChat, load_llm_config


def infer_filters(question: str) -> dict:
    compact = re.sub(r"\s+", "", question.lower())
    filters: dict = {}
    if "近7天" in compact or "last7days" in compact or "past7days" in compact:
        filters["start_date"] = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    elif "近30天" in compact or "last30days" in compact or "past30days" in compact:
        filters["start_date"] = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()

    categories = []
    if any(term in question.lower() for term in ["政策", "policy", "regulation", "export control"]):
        categories.append("policy")
    if any(term in question.lower() for term in ["价格", "price", "close", "market"]):
        categories.append("price")
    if any(term in question for term in ["收盘", "收盘价", "股价", "多少钱"]):
        categories.append("price")
    if re.search(r"\b(BHP|RIO|VALE|FCX|SCCO|ALB|SQM|LIT|COPX|PICK|GLNCY|NEM)\b", question, re.I):
        categories.append("price")
    if any(term in question.lower() for term in ["新闻", "news", "报道"]):
        categories.append("news")
    if categories:
        filters["categories"] = categories
    return filters


LLM_CACHE: dict[str, str] = {}
LLM_COOLDOWN_UNTIL: float = 0.0


def repair_mojibake(text: str) -> str:
    """Recover UTF-8 text that arrived as Windows/Latin-1 mojibake."""
    if not any(marker in text for marker in ("Ã", "Â", "å", "è", "æ", "ç")):
        return text
    for encoding in ("cp1252", "latin1"):
        try:
            repaired = text.encode(encoding).decode("utf-8")
        except UnicodeError:
            continue
        if any("\u4e00" <= char <= "\u9fff" for char in repaired):
            return repaired
    return text


def answer_question(
    db_path: str,
    question: str,
    top_k: int = 5,
    filters: dict | None = None,
    use_llm: bool = True,
) -> dict:
    question = repair_mojibake(question)
    inferred = infer_filters(question)
    merged_filters = {**inferred, **(filters or {})}
    query = expand_query(question)
    retrieval_note = None
    store = SQLiteVectorStore(db_path)
    try:
        hits = store.search(
            query=query,
            top_k=top_k,
            categories=merged_filters.get("categories"),
            start_date=merged_filters.get("start_date"),
            end_date=merged_filters.get("end_date"),
        )
        if not hits and (merged_filters.get("start_date") or merged_filters.get("end_date")):
            hits = store.search(
                query=query,
                top_k=top_k,
                categories=merged_filters.get("categories"),
            )
            if hits:
                retrieval_note = "严格时间范围内没有命中，已自动放宽时间过滤。"
        if not hits and merged_filters.get("categories"):
            hits = store.search(query=query, top_k=top_k)
            if hits:
                retrieval_note = "严格类别过滤内没有命中，已自动放宽类别过滤。"
    finally:
        store.close()

    answer, mode, error = generate_answer(question, hits, use_llm=use_llm)
    return {
        "question": question,
        "answer": answer,
        "answer_mode": mode,
        "llm_error": error,
        "retrieval_note": retrieval_note,
        "filters": merged_filters,
        "citations": [serialize_hit(hit) for hit in hits],
    }


def generate_answer(question: str, hits: list[SearchHit], use_llm: bool = True) -> tuple[str, str, str | None]:
    global LLM_COOLDOWN_UNTIL
    if not hits:
        return (
            "没有在当前向量库中找到足够相关的证据。请先运行采集任务，或放宽时间/类别过滤。",
            "empty",
            None,
        )
    if is_price_question(question, hits) and not use_llm:
        return synthesize_price_answer(question, hits), "local_price_summary", None
    if not use_llm:
        return synthesize_answer(question, hits), "local_summary", None

    config = load_llm_config()
    if config is None:
        return synthesize_natural_answer(question, hits), "local_natural_summary", None
    cache_key = llm_cache_key(question, hits)
    if cache_key in LLM_CACHE:
        return LLM_CACHE[cache_key], "llm_cached", None
    if datetime.now(timezone.utc).timestamp() < LLM_COOLDOWN_UNTIL:
        return synthesize_natural_answer(question, hits), "local_natural_summary", None

    try:
        answer = OpenAICompatibleChat(config).complete(build_rag_messages(question, hits))
        LLM_CACHE[cache_key] = answer
        return answer, "llm", None
    except Exception as exc:
        message = str(exc)
        if any(code in message for code in ("HTTP 429", "HTTP 500", "HTTP 502", "HTTP 503", "HTTP 504", "UNAVAILABLE")):
            LLM_COOLDOWN_UNTIL = datetime.now(timezone.utc).timestamp() + 20
            return synthesize_natural_answer(question, hits), "local_natural_summary", message
        return synthesize_natural_answer(question, hits), "local_natural_summary", message


def llm_cache_key(question: str, hits: list[SearchHit]) -> str:
    raw = question + "|" + "|".join(hit.document.id for hit in hits)
    return sha1(raw.encode("utf-8")).hexdigest()


def is_price_question(question: str, hits: list[SearchHit]) -> bool:
    if any(term in question for term in ("收盘", "收盘价", "股价", "价格", "多少钱")):
        return any(hit.document.category == "price" for hit in hits)
    return False


def synthesize_price_answer(question: str, hits: list[SearchHit]) -> str:
    price_hits = [hit for hit in hits if hit.document.category == "price"]
    price_hits.sort(key=lambda hit: hit.document.published_at, reverse=True)
    if not price_hits:
        return synthesize_answer(question, hits)

    latest = price_hits[0].document
    previous = price_hits[1].document if len(price_hits) > 1 else None
    symbol = latest.metadata.get("symbol", latest.title.split()[0])
    close = as_float(latest.metadata.get("close"))
    open_ = as_float(latest.metadata.get("open"))
    high = as_float(latest.metadata.get("high"))
    low = as_float(latest.metadata.get("low"))
    volume = latest.metadata.get("volume")

    lines = [
        f"{symbol} 最近一条价格记录是 {latest.published_at.date().isoformat()}，收盘价为 {format_number(close)}。",
    ]
    if previous:
        prev_close = as_float(previous.metadata.get("close"))
        if close is not None and prev_close:
            change = close - prev_close
            pct = change / prev_close * 100
            direction = "上涨" if change >= 0 else "下跌"
            lines.append(
                f"相比上一条记录 {previous.published_at.date().isoformat()} 的 {format_number(prev_close)}，"
                f"{direction} {format_number(abs(change))}，约 {format_number(abs(pct))}% 。"
            )
    detail_parts = []
    if open_ is not None:
        detail_parts.append(f"开盘 {format_number(open_)}")
    if high is not None:
        detail_parts.append(f"最高 {format_number(high)}")
    if low is not None:
        detail_parts.append(f"最低 {format_number(low)}")
    if volume is not None:
        detail_parts.append(f"成交量 {int(volume):,}")
    if detail_parts:
        lines.append("当日明细：" + "，".join(detail_parts) + "。")
    lines.append(f"数据来源：{latest.source}，引用见下方价格记录。")
    return "\n".join(lines)


def as_float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def format_number(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"{value:.2f}"


def build_rag_messages(question: str, hits: list[SearchHit]) -> list[dict[str, str]]:
    evidence_blocks = []
    for idx, hit in enumerate(hits, start=1):
        doc = hit.document
        metadata = {key: value for key, value in doc.metadata.items() if value is not None}
        evidence_blocks.append(
            "\n".join(
                [
                    f"[{idx}]",
                    f"title: {doc.title}",
                    f"category: {doc.category}",
                    f"source: {doc.source}",
                    f"published_at: {doc.published_at.date().isoformat()}",
                    f"url: {doc.url}",
                    f"metadata: {metadata}",
                    f"snippet: {doc.body[:900]}",
                ]
            )
        )
    evidence = "\n\n".join(evidence_blocks)
    system = (
        "你是矿业新闻、政策和价格情报分析助手。"
        "你只能根据用户提供的证据回答，不要编造证据外的信息。"
        "用自然、简洁的中文回答。"
        "不要在正文中插入 [1]、[2] 这样的编号引用，也不要另列参考来源；前端会单独展示证据卡片。"
        "如果证据不足，明确说证据不足，并说明还需要什么数据。"
        "涉及价格时说明日期、标的和 close/open/high/low/volume 中可用的字段。"
    )
    user = f"问题：{question}\n\n证据：\n{evidence}\n\n请只基于这些证据给出自然语言回答。"
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def synthesize_natural_answer(question: str, hits: list[SearchHit]) -> str:
    if not hits:
        return "没有在当前向量库中找到足够相关的证据。请先运行采集任务，或放宽时间/类别过滤。"

    if is_price_question(question, hits):
        return synthesize_price_answer(question, hits)

    sorted_hits = sorted(hits, key=lambda hit: hit.document.published_at, reverse=True)
    dates = [hit.document.published_at.date() for hit in sorted_hits]
    start = min(dates).isoformat()
    end = max(dates).isoformat()
    category_counts = count_categories(sorted_hits)
    category_text = "、".join(f"{label_category(category)} {count} 条" for category, count in category_counts)
    direct_gap = requested_scope_gap(question, sorted_hits)

    if direct_gap:
        opening = (
            f"从当前返回的 {len(sorted_hits)} 条证据看，暂时没有直接命中{direct_gap}的材料，"
            "所以不能据此判断这个具体问题已经发生了明确变化。"
        )
    else:
        opening = f"从当前返回的 {len(sorted_hits)} 条证据看，{start} 至 {end} 期间可用信息主要包括{category_text}。"

    highlights = []
    for hit in sorted_hits[:3]:
        doc = hit.document
        highlights.append(f"{doc.published_at.date().isoformat()} 的“{doc.title}”")

    if highlights:
        evidence_sentence = "比较接近的问题线索是：" + "；".join(highlights) + "。"
    else:
        evidence_sentence = ""

    if direct_gap:
        conclusion = "更稳妥的结论是：现有检索结果只能说明系统找到了相近的矿业贸易或政策信息，还不足以回答该具体地区、矿种或时间窗口内是否有新政策。建议继续补充更直接的官方公告、海关/出口规则或当地监管来源。"
    elif any(hit.document.category == "policy" for hit in sorted_hits):
        conclusion = "更稳妥的结论是：当前证据支持做初步政策监测，但仍需要把政策原文或官方公告作为最终确认依据。"
    elif any(hit.document.category == "news" for hit in sorted_hits):
        conclusion = "更稳妥的结论是：当前结果更像新闻线索汇总，可以用于判断关注方向，但不宜直接当作政策事实定论。"
    else:
        conclusion = "更稳妥的结论是：当前结果可以作为初步线索，后续仍应结合更直接的数据源复核。"

    return "\n".join(part for part in (opening, evidence_sentence, conclusion) if part)


def count_categories(hits: list[SearchHit]) -> list[tuple[str, int]]:
    order = ("policy", "news", "price")
    counts = {category: 0 for category in order}
    for hit in hits:
        counts[hit.document.category] = counts.get(hit.document.category, 0) + 1
    return [(category, counts[category]) for category in order if counts.get(category)]


def label_category(category: str) -> str:
    labels = {
        "policy": "政策",
        "news": "新闻",
        "price": "价格",
    }
    return labels.get(category, category)


def requested_scope_gap(question: str, hits: list[SearchHit]) -> str | None:
    lowered_question = question.lower()
    if (
        any(term in question for term in ("澳洲", "澳大利亚"))
        or any(term in lowered_question for term in ("australia", "australian"))
    ) and ("锂" in question or "lithium" in lowered_question) and ("出口" in question or "export" in lowered_question):
        required_groups = (
            ("澳洲", "澳大利亚", "Australia", "Australian"),
            ("锂", "lithium"),
            ("出口", "export"),
            ("政策", "policy", "regulation", "rule"),
        )
        if not any(hit_contains_all_groups(hit, required_groups) for hit in hits):
            return "澳洲锂出口政策"

    scope_terms = {
        "澳洲锂出口政策": ("澳洲", "澳大利亚", "Australia", "Australian"),
        "澳洲": ("澳洲", "澳大利亚", "Australia", "Australian"),
        "加拿大": ("加拿大", "Canada", "Canadian"),
        "美国": ("美国", "United States", "US ", "U.S.", "American"),
        "印尼": ("印尼", "印度尼西亚", "Indonesia", "Indonesian"),
    }
    haystack = "\n".join(f"{hit.document.title}\n{hit.document.body}" for hit in hits)
    for label, terms in scope_terms.items():
        if any(term in question for term in terms[:2]) or any(term.lower() in question.lower() for term in terms[2:]):
            if not any(term in haystack for term in terms):
                return label
    return None


def hit_contains_all_groups(hit: SearchHit, term_groups: tuple[tuple[str, ...], ...]) -> bool:
    haystack = f"{hit.document.title}\n{hit.document.body}"
    lowered = haystack.lower()
    for terms in term_groups:
        if not any(term in haystack or term.lower() in lowered for term in terms):
            return False
    return True


def synthesize_answer(question: str, hits: list[SearchHit]) -> str:
    if not hits:
        return "没有在当前向量库中找到足够相关的证据。请先运行采集任务，或放宽时间/类别过滤。"

    policy_hits = [hit for hit in hits if hit.document.category == "policy"]
    price_hits = [hit for hit in hits if hit.document.category == "price"]
    news_hits = [hit for hit in hits if hit.document.category == "news"]

    lines = []
    if policy_hits:
        lines.append("政策侧：")
        for hit in policy_hits[:3]:
            doc = hit.document
            lines.append(f"- {doc.published_at.date()}，{doc.title}")
    if price_hits:
        lines.append("价格侧：")
        for hit in price_hits[:3]:
            doc = hit.document
            close = as_float(doc.metadata.get("close"))
            symbol = doc.metadata.get("symbol", doc.title.split()[0])
            lines.append(f"- {doc.published_at.date()}，{symbol} close={format_number(close)}")
    if news_hits:
        lines.append("新闻侧：")
        for hit in news_hits[:3]:
            doc = hit.document
            lines.append(f"- {doc.published_at.date()}，{doc.title}")

    if not lines:
        for hit in hits[:3]:
            doc = hit.document
            lines.append(f"- {doc.published_at.date()}，{doc.title}")
    lines.append("结论：以上回答仅基于返回引用中的证据；若引用不足，应视为需要继续采集或人工复核。")
    return "\n".join(lines)


def serialize_hit(hit: SearchHit) -> dict:
    doc = hit.document
    return {
        "id": doc.id,
        "score": round(hit.score, 4),
        "source": doc.source,
        "category": doc.category,
        "title": doc.title,
        "url": doc.url,
        "published_at": doc.published_at.isoformat(),
        "metadata": doc.metadata,
        "snippet": doc.body[:350],
    }
