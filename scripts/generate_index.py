#!/usr/bin/env python3
"""Generate a static index2.html from soundon.xml and apple.json."""
from __future__ import annotations

import json
import re
import textwrap
from dataclasses import dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime
from html import escape as html_escape
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable, List
from zoneinfo import ZoneInfo
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parent.parent
RSS_PATH = ROOT / "soundon.xml"
APPLE_PATH = ROOT / "apple.json"
TEMPLATE_PATH = ROOT / "index_template.html"
OUTPUT_PATH = ROOT / "index.html"

ITUNES_NS = "{http://www.itunes.com/dtds/podcast-1.0.dtd}"
CONTENT_NS = "{http://purl.org/rss/1.0/modules/content/}"


class HTMLSanitizer(HTMLParser):
    """A minimal HTML sanitizer replicating the client-side rules."""

    FORBIDDEN_TAGS = {"script", "style", "iframe", "object", "embed", "link"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: List[str] = []
        self._skip_stack: List[str] = []

    def handle_starttag(self, tag: str, attrs: List[tuple[str, str | None]]) -> None:  # type: ignore[override]
        if self._skip_stack:
            if tag in self.FORBIDDEN_TAGS:
                self._skip_stack.append(tag)
            return
        if tag in self.FORBIDDEN_TAGS:
            self._skip_stack.append(tag)
            return
        safe_attrs = []
        for name, value in attrs:
            if value is None:
                continue
            lower_name = name.lower()
            if lower_name.startswith("on"):
                continue
            if lower_name in {"href", "src"} and value.lower().startswith("javascript:"):
                continue
            if lower_name == "style":
                continue
            safe_attrs.append((name, value))
        attr_text = "".join(
            f" {name}=\"{html_escape(value, quote=True)}\"" for name, value in safe_attrs
        )
        self._parts.append(f"<{tag}{attr_text}>")

    def handle_endtag(self, tag: str) -> None:  # type: ignore[override]
        if self._skip_stack:
            if tag == self._skip_stack[-1]:
                self._skip_stack.pop()
            return
        if tag in self.FORBIDDEN_TAGS:
            return
        self._parts.append(f"</{tag}>")

    def handle_startendtag(self, tag: str, attrs: List[tuple[str, str | None]]) -> None:  # type: ignore[override]
        if self._skip_stack or tag in self.FORBIDDEN_TAGS:
            return
        safe_attrs = []
        for name, value in attrs:
            if value is None:
                continue
            lower_name = name.lower()
            if lower_name.startswith("on"):
                continue
            if lower_name in {"href", "src"} and value.lower().startswith("javascript:"):
                continue
            if lower_name == "style":
                continue
            safe_attrs.append((name, value))
        attr_text = "".join(
            f" {name}=\"{html_escape(value, quote=True)}\"" for name, value in safe_attrs
        )
        self._parts.append(f"<{tag}{attr_text}/>")

    def handle_data(self, data: str) -> None:  # type: ignore[override]
        if self._skip_stack:
            return
        if data:
            self._parts.append(html_escape(data))

    def handle_entityref(self, name: str) -> None:  # type: ignore[override]
        if self._skip_stack:
            return
        self._parts.append(f"&{name};")

    def handle_charref(self, name: str) -> None:  # type: ignore[override]
        if self._skip_stack:
            return
        self._parts.append(f"&#{name};")

    def handle_comment(self, data: str) -> None:  # type: ignore[override]
        # Drop comments to match template sanitization intent.
        return

    def get_html(self) -> str:
        return "".join(self._parts).strip()


def sanitize_html(value: str | None) -> str:
    if not value:
        return ""
    sanitizer = HTMLSanitizer()
    sanitizer.feed(value)
    sanitizer.close()
    return sanitizer.get_html()


def parse_keywords(value: str | None) -> List[str]:
    if not value:
        return []
    seen = set()
    keywords: List[str] = []
    for raw in re.split(r"[,，]", value):
        keyword = raw.strip()
        if not keyword or keyword in seen:
            continue
        seen.add(keyword)
        keywords.append(keyword)
    return keywords


def normalize_guid(value: str | None) -> str:
    return value.strip().lower() if value else ""


WEEKDAY_MAP = {
    0: "週一",
    1: "週二",
    2: "週三",
    3: "週四",
    4: "週五",
    5: "週六",
    6: "週日",
}


TAIPEI_TZ = ZoneInfo("Asia/Taipei")


def format_date(value: str | None) -> str:
    if not value:
        return ""
    try:
        dt = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return value
    if dt is None:
        return value
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo("UTC"))
    dt = dt.astimezone(TAIPEI_TZ)
    weekday = WEEKDAY_MAP.get(dt.weekday())
    if not weekday:
        return value
    return f"{dt.year}年{dt.month}月{dt.day}日 {weekday}"


def format_duration(value: str | None) -> str:
    if not value:
        return ""
    raw = value.strip()
    if not raw:
        return ""
    total_seconds = 0
    if raw.isdigit():
        total_seconds = int(raw)
    else:
        parts = [part.strip() for part in raw.split(":")]
        ints = []
        for part in parts:
            if part.isdigit():
                ints.append(int(part))
            else:
                ints.append(0)
        if len(ints) == 3:
            total_seconds = ints[0] * 3600 + ints[1] * 60 + ints[2]
        elif len(ints) == 2:
            total_seconds = ints[0] * 60 + ints[1]
        elif len(ints) == 1:
            total_seconds = ints[0]
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60
    segments = []
    if hours:
        segments.append(f"{hours} 小時")
    if minutes:
        segments.append(f"{minutes} 分")
    if not hours and seconds:
        segments.append(f"{seconds} 秒")
    return " ".join(segments)


@dataclass
class Episode:
    title: str
    pub_date: str
    duration: str
    description_html: str
    summary: str
    keywords: List[str]
    cover: str
    apple_link: str
    guid: str


def indent_lines(content: Iterable[str], indent: str) -> List[str]:
    return [f"{indent}{line}" if line else indent for line in content]


def render_description(description_html: str, summary: str, indent: str) -> List[str]:
    lines: List[str] = [f"{indent}<div class=\"episode-description\">"]
    inner_indent = indent + "  "
    if description_html:
        lines.extend(indent_lines(description_html.strip().splitlines(), inner_indent))
    elif summary:
        lines.append(f"{inner_indent}{html_escape(summary)}")
    lines.append(f"{indent}</div>")
    return lines


def render_episode_card(episode: Episode, base_indent: str = "          ") -> str:
    lines: List[str] = []
    level1 = base_indent + "  "
    level2 = level1 + "  "
    dataset_title = html_escape(episode.title, quote=True)
    keywords_json = html_escape(json.dumps(episode.keywords, ensure_ascii=False), quote=True)
    lines.append(
        f"{base_indent}<article class=\"episode-card\" data-title=\"{dataset_title}\" data-keywords=\"{keywords_json}\">"
    )
    if episode.cover:
        lines.append(f"{level1}<div class=\"episode-cover\">")
        alt_text = html_escape(f"{episode.title} 封面", quote=True)
        cover_src = html_escape(episode.cover, quote=True)
        lines.append(f"{level2}<img src=\"{cover_src}\" alt=\"{alt_text}\" />")
        lines.append(f"{level1}</div>")
    meta_segments: List[str] = []
    if episode.pub_date:
        meta_segments.append(f"{level2}<span>{html_escape(episode.pub_date)}</span>")
    if episode.duration:
        meta_segments.append(f"{level2}<span>節目長度：{html_escape(episode.duration)}</span>")
    if meta_segments:
        lines.append(f"{level1}<div class=\"episode-meta\">")
        lines.extend(meta_segments)
        lines.append(f"{level1}</div>")
    lines.append(f"{level1}<h3 class=\"episode-title\">{html_escape(episode.title)}</h3>")
    lines.extend(render_description(episode.description_html, episode.summary, level1))
    if episode.apple_link:
        link_href = html_escape(episode.apple_link, quote=True)
        aria_label = html_escape(f"在 Apple Podcasts 播放〈{episode.title}〉", quote=True)
        lines.append(f"{level1}<div class=\"episode-actions\">")
        lines.append(
            f"{level2}<a class=\"episode-action\" href=\"{link_href}\" target=\"_blank\" rel=\"noopener noreferrer\" aria-label=\"{aria_label}\">"
        )
        lines.append(f"{level2}  <span class=\"icon\" aria-hidden=\"true\">▶️</span>")
        sr_text = html_escape(f"在 Apple Podcasts 播放〈{episode.title}〉")
        lines.append(f"{level2}  <span class=\"sr-only\">{sr_text}</span>")
        lines.append(f"{level2}</a>")
        lines.append(f"{level1}</div>")
    if episode.keywords:
        keyword_text = html_escape("、".join(episode.keywords))
        lines.append(f"{level1}<div class=\"episode-keywords\">關鍵字：{keyword_text}</div>")
    lines.append(f"{base_indent}</article>")
    return "\n".join(lines)


def load_template() -> str:
    return TEMPLATE_PATH.read_text(encoding="utf-8")


def main() -> None:
    template_html = load_template()

    rss_tree = ET.parse(RSS_PATH)
    channel = rss_tree.getroot().find("channel")
    if channel is None:
        raise RuntimeError("Invalid RSS feed: missing channel element")

    show_title = (channel.findtext("title") or "").strip() or "科學好好聽"
    show_description_raw = channel.findtext("description")
    show_description_html = sanitize_html(show_description_raw)
    show_author = (channel.findtext(f"{ITUNES_NS}author") or "").strip()
    show_image = channel.findtext("image/url") or ""
    itunes_image = channel.find(f"{ITUNES_NS}image")
    if itunes_image is not None:
        show_image = itunes_image.attrib.get("href", show_image)
    show_link = (channel.findtext("link") or "").strip()
    language = (channel.findtext("language") or "zh-Hant").strip() or "zh-Hant"
    summary_text = channel.findtext(f"{ITUNES_NS}summary") or show_description_raw or ""

    with APPLE_PATH.open("r", encoding="utf-8") as fp:
        apple_payload = json.load(fp)
    apple_episode_map = {
        normalize_guid(item.get("episodeGuid")): item.get("trackViewUrl", "")
        for item in apple_payload.get("results", [])
        if item.get("episodeGuid") and item.get("trackViewUrl")
    }

    episodes: List[Episode] = []
    all_keywords = set()
    for item in channel.findall("item"):
        title = (item.findtext("title") or "未命名集數").strip()
        pub_date = format_date(item.findtext("pubDate"))
        duration = format_duration(item.findtext(f"{ITUNES_NS}duration"))
        raw_description = item.findtext(f"{CONTENT_NS}encoded") or item.findtext("description") or ""
        sanitized_description = sanitize_html(raw_description)
        summary = (item.findtext(f"{ITUNES_NS}summary") or "").strip()
        raw_keywords = item.findtext(f"{ITUNES_NS}keywords") or ""
        keywords = parse_keywords(raw_keywords)
        cover_element = item.find(f"{ITUNES_NS}image")
        cover = ""
        if cover_element is not None:
            cover = cover_element.attrib.get("href", "")
        guid = normalize_guid(item.findtext("guid"))
        apple_link = apple_episode_map.get(guid, "")
        episodes.append(
            Episode(
                title=title,
                pub_date=pub_date,
                duration=duration,
                description_html=sanitized_description,
                summary=summary,
                keywords=keywords,
                cover=cover,
                apple_link=apple_link,
                guid=guid,
            )
        )
        all_keywords.update(keywords)

    episodes_markup = "\n".join(render_episode_card(ep) for ep in episodes)

    now_year = datetime.now(TAIPEI_TZ).year

    structured_data: dict[str, object] = {
        "@context": "https://schema.org/",
        "@type": "PodcastSeries",
        "name": show_title,
        "url": show_link,
        "image": show_image,
        "inLanguage": language,
        "description": (summary_text or show_description_raw or "").strip(),
    }
    if show_author:
        structured_data["author"] = {"@type": "Person", "name": show_author}
    if show_link:
        structured_data["potentialAction"] = [{"@type": "ListenAction", "target": [show_link]}]

    structured_data_json = json.dumps(structured_data, ensure_ascii=False, indent=2)
    structured_data_block = "\n".join(
        f"      {line}" for line in structured_data_json.splitlines()
    )

    def replace(pattern: str, repl: str, text: str) -> str:
        return re.sub(pattern, repl, text, count=1, flags=re.DOTALL)

    result = template_html
    result = replace(
        r'(<a class="brand" href="#top">)(.*?)(</a>)',
        rf"\1{html_escape(show_title)}\3",
        result,
    )
    result = replace(
        r'(<h1 id="show-title">)(.*?)(</h1>)',
        rf"\1{html_escape(show_title)}\3",
        result,
    )

    if show_description_html:
        indented_description = "\n".join(
            f"          {line}" for line in show_description_html.strip().splitlines()
        )
    else:
        indented_description = "          "
    result = replace(
        r'(<p id="show-description">)(.*?)(</p>)',
        f"\\1\n{indented_description}\n        \\3",
        result,
    )

    author_text = f"主持：{show_author}" if show_author else ""
    result = replace(
        r'(<div class="host-info" id="show-author">)(.*?)(</div>)',
        rf"\1{html_escape(author_text)}\3",
        result,
    )

    cover_alt = html_escape(f"{show_title} 封面", quote=True)
    cover_src = html_escape(show_image, quote=True)
    result = replace(
        r'<img id="show-cover"[^>]*?>',
        f'<img id="show-cover" src="{cover_src}" alt="{cover_alt}" />',
        result,
    )

    if show_link:
        result = replace(
            r'(<footer[\s\S]*?<a href=")([^\"]*)("[^>]*>)',
            rf"\1{html_escape(show_link, quote=True)}\3",
            result,
        )

    result = replace(
        r'©\s*<span id="copyright-year">.*?</span>',
        f'© {now_year}',
        result,
    )

    result = replace(
        r'(<script type="application/ld\+json" id="structured-data">)(.*?)(</script>)',
        f"\\1\n{structured_data_block}\n    \\3",
        result,
    )

    episodes_replacement = f"\\1\n{episodes_markup}\n        \\3" if episodes_markup else "\\1\\n        \\3"
    result = replace(
        r'(<div id="episodes"[^>]*>)(.*?)(\n        </div>)',
        episodes_replacement,
        result,
    )

    if all_keywords:
        result = replace(
            r'<div class="tag-search" id="tag-search" hidden>',
            '<div class="tag-search" id="tag-search">',
            result,
        )

    static_script = textwrap.dedent("""
(() => {
  const episodesContainer = document.getElementById('episodes');
  const tagSearchWrapper = document.getElementById('tag-search');
  const selectedTagsContainer = document.getElementById('selected-tags');
  const tagInput = document.getElementById('tag-input');
  const suggestionsList = document.getElementById('tag-suggestions');
  const noResultsEl = document.getElementById('no-results');
  const episodesData = [];
  const allKeywordsSet = new Set();
  const activeTags = [];
  const activeTagSet = new Set();
  let highlightedSuggestionIndex = -1;
  const tagInputDefaultPlaceholder = tagInput?.getAttribute('placeholder') || '';
  const TAG_INPUT_DISABLED_PLACEHOLDER = '目前沒有可用關鍵字';
  const TAG_SUGGESTION_LIMIT = 100;
  let keywordDataReady = false;

  function getAvailableTags() {
    return Array.from(allKeywordsSet).filter((tag) => !activeTagSet.has(tag));
  }

  function renderSelectedTags() {
    if (!selectedTagsContainer) return;
    selectedTagsContainer.innerHTML = '';
    activeTags.forEach((tag) => {
      const chip = document.createElement('button');
      chip.type = 'button';
      chip.className = 'tag-chip';
      chip.setAttribute('data-tag', tag);
      chip.setAttribute('aria-label', `移除關鍵字 ${tag}`);
      chip.innerHTML = `<span>${tag}</span><span class="tag-chip-remove" aria-hidden="true">×</span>`;
      selectedTagsContainer.appendChild(chip);
    });
  }

  function filterEpisodes() {
    if (!episodesContainer) return;
    const hasActiveTags = activeTags.length > 0;
    let visibleCount = 0;
    episodesData.forEach(({ element, keywords }) => {
      const matches = !hasActiveTags || keywords.some((keyword) => activeTagSet.has(keyword));
      element.hidden = !matches;
      if (matches) {
        visibleCount += 1;
      }
    });
    if (noResultsEl) {
      if (!hasActiveTags) {
        noResultsEl.hidden = true;
      } else {
        noResultsEl.hidden = visibleCount > 0;
      }
    }
  }

  function setTagInputAvailability(isReady) {
    if (!tagInput) return;
    if (isReady) {
      tagInput.disabled = false;
      tagInput.removeAttribute('aria-disabled');
      if (tagInputDefaultPlaceholder) {
        tagInput.setAttribute('placeholder', tagInputDefaultPlaceholder);
      } else {
        tagInput.removeAttribute('placeholder');
      }
    } else {
      tagInput.disabled = true;
      tagInput.setAttribute('aria-disabled', 'true');
      tagInput.setAttribute('placeholder', TAG_INPUT_DISABLED_PLACEHOLDER);
      tagInput.value = '';
    }
  }

  function updateSuggestions() {
    if (!tagInput || !suggestionsList) return;
    if (!keywordDataReady) {
      suggestionsList.hidden = true;
      tagInput.setAttribute('aria-expanded', 'false');
      highlightedSuggestionIndex = -1;
      tagInput.removeAttribute('aria-activedescendant');
      return;
    }
    suggestionsList.innerHTML = '';
    highlightedSuggestionIndex = -1;
    tagInput.removeAttribute('aria-activedescendant');
    const availableTags = getAvailableTags();
    if (!availableTags.length) {
      suggestionsList.hidden = true;
      tagInput.setAttribute('aria-expanded', 'false');
      return;
    }
    const query = tagInput.value.trim().toLowerCase();
    const matches = query
      ? availableTags.filter((tag) => tag.toLowerCase().includes(query))
      : availableTags;
    const limited = matches.slice(0, TAG_SUGGESTION_LIMIT);
    if (!limited.length || document.activeElement !== tagInput) {
      suggestionsList.hidden = true;
      tagInput.setAttribute('aria-expanded', 'false');
      return;
    }
    limited.forEach((tag, index) => {
      const option = document.createElement('li');
      option.className = 'tag-suggestion';
      option.id = `tag-suggestion-${index}`;
      option.setAttribute('role', 'option');
      option.textContent = tag;
      suggestionsList.appendChild(option);
    });
    suggestionsList.hidden = false;
    tagInput.setAttribute('aria-expanded', 'true');
  }

  function highlightSuggestion(index) {
    if (!suggestionsList) return;
    const options = Array.from(suggestionsList.querySelectorAll('.tag-suggestion'));
    if (!options.length) {
      highlightedSuggestionIndex = -1;
      tagInput?.removeAttribute('aria-activedescendant');
      return;
    }
    if (index < 0) {
      index = options.length - 1;
    } else if (index >= options.length) {
      index = 0;
    }
    highlightedSuggestionIndex = index;
    options.forEach((option, optionIndex) => {
      option.classList.toggle('is-highlighted', optionIndex === highlightedSuggestionIndex);
    });
    const activeOption = options[highlightedSuggestionIndex];
    if (activeOption) {
      activeOption.scrollIntoView({ block: 'nearest' });
      tagInput?.setAttribute('aria-activedescendant', activeOption.id);
    } else {
      tagInput?.removeAttribute('aria-activedescendant');
    }
  }

  function findBestMatch(query) {
    if (!query) return null;
    const availableTags = getAvailableTags();
    const lowerCaseQuery = query.toLowerCase();
    return (
      availableTags.find((tag) => tag.toLowerCase() === lowerCaseQuery) ||
      availableTags.find((tag) => tag.toLowerCase().startsWith(lowerCaseQuery)) ||
      availableTags.find((tag) => tag.toLowerCase().includes(lowerCaseQuery)) ||
      null
    );
  }

  function addTag(tag) {
    if (!tag || activeTagSet.has(tag)) {
      return;
    }
    activeTags.push(tag);
    activeTagSet.add(tag);
    renderSelectedTags();
    filterEpisodes();
    if (tagInput) {
      tagInput.value = '';
      tagInput.focus();
    }
    updateSuggestions();
  }

  function removeTag(tag) {
    if (!activeTagSet.has(tag)) {
      return;
    }
    activeTagSet.delete(tag);
    const index = activeTags.indexOf(tag);
    if (index !== -1) {
      activeTags.splice(index, 1);
    }
    renderSelectedTags();
    filterEpisodes();
    updateSuggestions();
  }

  function handleTagInputKeydown(event) {
    if (!tagInput) return;
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      if (suggestionsList?.hidden) {
        updateSuggestions();
      }
      const options = suggestionsList ? suggestionsList.querySelectorAll('.tag-suggestion') : [];
      if (!options.length) {
        return;
      }
      event.preventDefault();
      const nextIndex =
        event.key === 'ArrowDown'
          ? highlightedSuggestionIndex + 1
          : highlightedSuggestionIndex === -1
          ? options.length - 1
          : highlightedSuggestionIndex - 1;
      highlightSuggestion(nextIndex);
    } else if (
      event.key === 'Enter' ||
      event.key === ',' ||
      (event.key === 'Tab' && tagInput.value.trim())
    ) {
      const options = suggestionsList ? suggestionsList.querySelectorAll('.tag-suggestion') : [];
      const highlightedOption =
        highlightedSuggestionIndex >= 0 && options[highlightedSuggestionIndex]
          ? options[highlightedSuggestionIndex].textContent
          : null;
      const candidate = highlightedOption?.trim() || findBestMatch(tagInput.value.trim());
      if (candidate) {
        event.preventDefault();
        addTag(candidate);
      } else if (event.key === ',') {
        event.preventDefault();
      }
    } else if (event.key === 'Backspace' && !tagInput.value && activeTags.length) {
      event.preventDefault();
      removeTag(activeTags[activeTags.length - 1]);
    } else if (event.key === 'Escape') {
      if (suggestionsList) {
        suggestionsList.hidden = true;
        suggestionsList.innerHTML = '';
      }
      highlightedSuggestionIndex = -1;
      tagInput.setAttribute('aria-expanded', 'false');
      tagInput.removeAttribute('aria-activedescendant');
    }
  }

  function initializeTagSearch() {
    if (!tagInput || !suggestionsList) return;
    renderSelectedTags();
    tagInput.addEventListener('input', () => {
      highlightedSuggestionIndex = -1;
      updateSuggestions();
    });
    tagInput.addEventListener('focus', () => {
      highlightedSuggestionIndex = -1;
      updateSuggestions();
    });
    tagInput.addEventListener('blur', () => {
      setTimeout(() => {
        if (suggestionsList) {
          suggestionsList.hidden = true;
          suggestionsList.innerHTML = '';
        }
        tagInput.setAttribute('aria-expanded', 'false');
        highlightedSuggestionIndex = -1;
        tagInput.removeAttribute('aria-activedescendant');
      }, 120);
    });
    tagInput.addEventListener('keydown', handleTagInputKeydown);
    suggestionsList.addEventListener('mousedown', (event) => {
      event.preventDefault();
    });
    suggestionsList.addEventListener('click', (event) => {
      const option = event.target.closest('.tag-suggestion');
      if (!option) return;
      addTag(option.textContent.trim());
    });
    selectedTagsContainer?.addEventListener('click', (event) => {
      const chip = event.target.closest('.tag-chip');
      if (!chip) return;
      const tag = chip.getAttribute('data-tag');
      if (tag) {
        removeTag(tag);
        tagInput.focus();
      }
    });
  }

  function initializeEpisodes() {
    if (!episodesContainer) return;
    episodesData.length = 0;
    allKeywordsSet.clear();
    const cards = episodesContainer.querySelectorAll('.episode-card');
    cards.forEach((card) => {
      const keywordList = card.dataset.keywords ? JSON.parse(card.dataset.keywords) : [];
      episodesData.push({ element: card, keywords: keywordList });
      keywordList.forEach((keyword) => allKeywordsSet.add(keyword));
    });
    keywordDataReady = allKeywordsSet.size > 0;
    if (tagSearchWrapper) {
      tagSearchWrapper.hidden = allKeywordsSet.size === 0;
    }
    setTagInputAvailability(keywordDataReady);
    filterEpisodes();
    updateSuggestions();
  }

  initializeTagSearch();
  initializeEpisodes();
})();
""").strip("\n")
    static_script = textwrap.indent(static_script, "      ")
    result = re.sub(
        r'(\s*<script>\s*const structuredDataEl[\s\S]*?</script>)',
        '    <script>\n' + static_script + '\n    </script>',
        result,
        count=1
    )

    OUTPUT_PATH.write_text(result, encoding="utf-8")


if __name__ == "__main__":
    main()
