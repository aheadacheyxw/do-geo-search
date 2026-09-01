"""Audit only citation candidates that are visibly connected to an answer."""

from __future__ import annotations

from html import unescape
from html.parser import HTMLParser
import json
import re
from typing import Any, Mapping, Sequence


class _VisibleSourceCardParser(HTMLParser):
    """Extract links only from the platform's open, visible source-card rail.

    It never treats arbitrary page ``href`` values as citations.  A link is
    admitted only when it is the anchor of a visible ``site-item`` inside an
    open citation panel and it has a visible title or publisher label.
    """

    def __init__(self) -> None:
        super().__init__()
        self.stack: list[dict[str, Any]] = []
        self.active: dict[str, Any] | None = None
        self.cards: list[dict[str, Any]] = []

    # HTMLParser reports void tags through ``handle_starttag`` but never emits
    # an end tag for them.  They must not participate in the nesting stack:
    # otherwise an icon such as ``<img>`` inside a source card shifts every
    # following closing tag and makes a real visible card look out of scope.
    _VOID_TAGS = {
        "area", "base", "br", "col", "embed", "hr", "img", "input",
        "link", "meta", "param", "source", "track", "wbr",
    }

    def _has_ancestor_class(self, *needles: str) -> bool:
        classes = " ".join(str(item.get("class", "")) for item in self.stack)
        return all(needle in classes for needle in needles)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        record = {key: value or "" for key, value in attrs}
        record["tag"] = tag
        if tag not in self._VOID_TAGS:
            self.stack.append(record)
        classes = str(record.get("class", ""))
        if (
            tag == "a" and "site-item" in classes and str(record.get("href", "")).startswith(("http://", "https://"))
            and self._has_ancestor_class("side-console-rail", "open", "ref", "sites")
        ):
            self.active = {
                "depth": len(self.stack), "url": record["href"],
                "title_chunks": [], "publisher_chunks": [], "fallback_chunks": [],
            }

    def handle_data(self, data: str) -> None:
        if not self.active:
            return
        value = " ".join(data.split())
        if not value:
            return
        classes = " ".join(str(item.get("class", "")) for item in self.stack)
        if "site-title" in classes:
            self.active["title_chunks"].append(value)
        elif "site-name-text" in classes or "site-name" in classes:
            self.active["publisher_chunks"].append(value)
        else:
            self.active["fallback_chunks"].append(value)

    def handle_endtag(self, tag: str) -> None:
        if self.active and len(self.stack) == self.active["depth"] and tag == "a":
            title = " ".join(self.active["title_chunks"] or self.active["fallback_chunks"])
            publisher = " ".join(self.active["publisher_chunks"])
            if title or publisher:
                self.cards.append({
                    "visible_url": self.active["url"],
                    "visible_anchor_text": title or publisher,
                    "visible_domain_text": publisher or None,
                    "source_card_status": "verified_derived_visible_source_card",
                    "derivation_basis": "open_visible_source_panel_site_item_anchor",
                })
            self.active = None
        # Pop through the matching element instead of blindly popping one
        # level.  Captured UI DOM can be imperfect; preserving the enclosing
        # visible-source rail is safer than accepting an unrelated anchor.
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index].get("tag") == tag:
                del self.stack[index:]
                break


class _DeepSeekSourceCardParser(HTMLParser):
    """Extract DeepSeek's visible search-result cards, never inline page links."""

    _VOID_TAGS = _VisibleSourceCardParser._VOID_TAGS

    def __init__(self) -> None:
        super().__init__()
        self.stack: list[dict[str, Any]] = []
        self.active: dict[str, Any] | None = None
        self.cards: list[dict[str, Any]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        record = {key: value or "" for key, value in attrs}
        record["tag"] = tag
        if tag not in self._VOID_TAGS:
            self.stack.append(record)
        if tag == "a" and str(record.get("href", "")).startswith(("http://", "https://")):
            self.active = {
                "depth": len(self.stack), "url": record["href"], "title_chunks": [],
                "has_title": False, "has_snippet": False,
            }

    def handle_data(self, data: str) -> None:
        if not self.active:
            return
        value = " ".join(data.split())
        if not value:
            return
        classes = " ".join(str(item.get("class", "")) for item in self.stack)
        if "search-view-card__title" in classes:
            self.active["has_title"] = True
            self.active["title_chunks"].append(value)
        elif "search-view-card__snippet" in classes:
            self.active["has_snippet"] = True

    def handle_endtag(self, tag: str) -> None:
        if self.active and len(self.stack) == self.active["depth"] and tag == "a":
            if self.active["has_title"] and self.active["has_snippet"]:
                self.cards.append({
                    "visible_url": self.active["url"],
                    "visible_anchor_text": " ".join(self.active["title_chunks"]),
                    "visible_domain_text": None,
                    "source_card_status": "verified_derived_platform_bound_source_card",
                    "derivation_basis": "deepseek_visible_search_view_card_anchor",
                    "url_display_state": "platform_bound_not_literal_text",
                })
            self.active = None
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index].get("tag") == tag:
                del self.stack[index:]
                break


class _QianwenSourceCardParser(HTMLParser):
    """Extract only cards inside 千问's expanded ``参考来源`` panel."""

    _VOID_TAGS = _VisibleSourceCardParser._VOID_TAGS

    def __init__(self) -> None:
        super().__init__()
        self.stack: list[dict[str, Any]] = []
        self.active: dict[str, Any] | None = None
        self.cards: list[dict[str, Any]] = []

    def _is_inside_reference_panel(self) -> bool:
        return any("deep-think-source" in str(item.get("class", "")) for item in self.stack)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        record = {key: value or "" for key, value in attrs}
        record["tag"] = tag
        if tag not in self._VOID_TAGS:
            self.stack.append(record)
        if not (tag == "div" and "source-item-" in str(record.get("class", "")) and self._is_inside_reference_panel()):
            return
        try:
            metadata = json.loads(unescape(str(record.get("data-click-extra", ""))))
        except json.JSONDecodeError:
            return
        url = str(metadata.get("url") or metadata.get("ref_url") or "")
        title = str(metadata.get("title") or "").strip()
        if url.startswith(("http://", "https://")) and title:
            self.active = {"depth": len(self.stack), "url": url, "title": title}

    def handle_endtag(self, tag: str) -> None:
        if self.active and len(self.stack) == self.active["depth"] and tag == "div":
            self.cards.append({
                "visible_url": self.active["url"],
                "visible_anchor_text": self.active["title"],
                "visible_domain_text": None,
                "source_card_status": "verified_derived_platform_bound_source_card",
                "derivation_basis": "qianwen_open_reference_card_metadata",
                "url_display_state": "platform_bound_not_literal_text",
            })
            self.active = None
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index].get("tag") == tag:
                del self.stack[index:]
                break


class _YuanbaoSourceCardParser(HTMLParser):
    """Extract cards only from an open 腾讯元宝 reference popup."""

    _VOID_TAGS = _VisibleSourceCardParser._VOID_TAGS

    def __init__(self) -> None:
        super().__init__()
        self.stack: list[dict[str, Any]] = []
        self.active: dict[str, Any] | None = None
        self.cards: list[dict[str, Any]] = []

    def _is_inside_open_popup(self) -> bool:
        has_popup = any("t-popup" in str(item.get("class", "")) and item.get("data-popper-reference-hidden") == "false" for item in self.stack)
        has_ref_container = any("hyc-common-markdown__ref-list__tip-container" in str(item.get("class", "")) for item in self.stack)
        return has_popup and has_ref_container

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        record = {key: value or "" for key, value in attrs}
        record["tag"] = tag
        if tag not in self._VOID_TAGS:
            self.stack.append(record)
        if (
            tag == "div"
            and "hyc-common-markdown__ref_card" in str(record.get("class", ""))
            and self._is_inside_open_popup()
            and str(record.get("data-url", "")).startswith(("http://", "https://"))
        ):
            self.active = {
                "depth": len(self.stack), "url": record["data-url"],
                "title_chunks": [], "publisher_chunks": [],
            }

    def handle_data(self, data: str) -> None:
        if not self.active:
            return
        value = " ".join(data.split())
        if not value:
            return
        classes = " ".join(str(item.get("class", "")) for item in self.stack)
        if "hyc-common-markdown__ref_card-title" in classes:
            self.active["title_chunks"].append(value)
        elif "hyc-common-markdown__ref_card-foot__source_txt" in classes:
            self.active["publisher_chunks"].append(value)

    def handle_endtag(self, tag: str) -> None:
        if self.active and len(self.stack) == self.active["depth"] and tag == "div":
            title = " ".join(self.active["title_chunks"])
            publisher = " ".join(self.active["publisher_chunks"])
            if title:
                self.cards.append({
                    "visible_url": self.active["url"],
                    "visible_anchor_text": title,
                    "visible_domain_text": publisher or None,
                    "source_card_status": "verified_derived_platform_bound_source_card",
                    "derivation_basis": "yuanbao_open_reference_popup_card",
                    "url_display_state": "platform_bound_not_literal_text",
                })
            self.active = None
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index].get("tag") == tag:
                del self.stack[index:]
                break


class _DoubaoSourceCardParser(HTMLParser):
    """Extract a 豆包 search-result item only after its visible panel opened.

    A 豆包 result is platform-bound by the search-result block and its
    ``data-tool-call-item-id``.  This deliberately excludes ordinary anchors,
    runtime links, and the collapsed source summary control.
    """

    _VOID_TAGS = _VisibleSourceCardParser._VOID_TAGS
    _RESULT_ID = re.compile(r"-result-\d+$")

    def __init__(self) -> None:
        super().__init__()
        self.stack: list[dict[str, Any]] = []
        self.active: dict[str, Any] | None = None
        self.cards: list[dict[str, Any]] = []

    def _is_inside_search_result_block(self) -> bool:
        return any(
            "search_query_result_block" in str(item.get("data-plugin-identifier", ""))
            for item in self.stack
        )

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        record = {key: value or "" for key, value in attrs}
        record["tag"] = tag
        if tag not in self._VOID_TAGS:
            self.stack.append(record)
        item_id = str(record.get("data-tool-call-item-id", ""))
        if (
            tag == "a"
            and self._is_inside_search_result_block()
            and record.get("data-thinking-box-tool-call") == "true"
            and self._RESULT_ID.search(item_id)
            and str(record.get("href", "")).startswith(("http://", "https://"))
        ):
            self.active = {
                "depth": len(self.stack), "url": record["href"], "title_chunks": [],
            }

    def handle_data(self, data: str) -> None:
        if not self.active:
            return
        value = " ".join(data.split())
        if value:
            self.active["title_chunks"].append(value)

    def handle_endtag(self, tag: str) -> None:
        if self.active and len(self.stack) == self.active["depth"] and tag == "a":
            title = re.sub(r"^\s*\d+\.\s*", "", " ".join(self.active["title_chunks"])).strip()
            if title:
                self.cards.append({
                    "visible_url": self.active["url"],
                    "visible_anchor_text": title,
                    "visible_domain_text": None,
                    "source_card_status": "verified_derived_platform_bound_source_card",
                    "derivation_basis": "doubao_expanded_search_reference_item",
                    "url_display_state": "platform_bound_not_literal_text",
                })
            self.active = None
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index].get("tag") == tag:
                del self.stack[index:]
                break


def derive_visible_source_cards(expanded_dom: str, observation_id: str) -> list[dict[str, Any]]:
    """Derive safely scoped source cards from a captured expanded source panel."""

    parsers = (
        _VisibleSourceCardParser(), _DeepSeekSourceCardParser(),
        _QianwenSourceCardParser(), _YuanbaoSourceCardParser(), _DoubaoSourceCardParser(),
    )
    for parser in parsers:
        # HTMLParser decodes text and attribute entities itself.  Decoding the
        # whole document first corrupts JSON held in quoted data attributes.
        parser.feed(expanded_dom)
    cards: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for parser in parsers:
        for card in parser.cards:
            key = (str(card["visible_url"]), str(card["visible_anchor_text"]))
            if key in seen:
                continue
            seen.add(key)
            cards.append({"source_card_id": f"{observation_id}:derived-visible-source:{len(cards) + 1}", **card})
    return cards


def audit_candidates(
    candidates: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split visible, contextual HTTP(S) citations from rejected candidates."""

    verified: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for candidate in candidates:
        record = dict(candidate)
        origin = record.get("candidate_origin")
        url = str(record.get("url", ""))
        anchor = record.get("anchor_or_span")
        kind = record.get("kind")
        if kind in {"runtime", "resource", "tracking", "login", "captcha"}:
            record.update(candidate_status="rejected", rejection_reason=f"{kind}_url")
            rejected.append(record)
        elif origin not in {"answer_text", "visible_source_card"}:
            record.update(candidate_status="rejected", rejection_reason="not_visible")
            rejected.append(record)
        elif not url.startswith(("http://", "https://")):
            record.update(candidate_status="rejected", rejection_reason="malformed")
            rejected.append(record)
        elif not anchor:
            record.update(candidate_status="rejected", rejection_reason="context_free")
            rejected.append(record)
        else:
            record.update(candidate_status="verified", rejection_reason=None)
            verified.append(record)
    return verified, rejected
