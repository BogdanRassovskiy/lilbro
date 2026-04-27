from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Iterable, List, Optional
import re
from urllib.parse import urljoin, urlsplit, urlunsplit

from .playwright_chromium import chromium_launch_kwargs


@dataclass(frozen=True)
class ParsedContent:
    source_url: str
    text: str
    links: List[str]
    profile: dict

    def to_json_dict(self) -> dict:
        return {
            "source_url": self.source_url,
            "links": self.links,
            "text": self.text,
            **self.profile,
        }


class _LinkExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag.lower() != "a":
            return
        href = ""
        for k, v in attrs:
            if k and k.lower() == "href":
                href = (v or "").strip()
                break
        if href:
            self.links.append(href)


_IGNORED_PATH_PREFIXES = ("/blog", "/career", "/privacy")
_PRIORITY_PATH_PREFIXES = ("/about", "/services", "/sluzby", "/o-nas", "/kontakt")
_EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
_PHONE_RE = re.compile(r"(?:\+?\d[\d\-\s()]{7,}\d)")


def _empty_profile() -> dict:
    return {
        "business_type": None,
        "services": [],
        "target_audience": None,
        "has_website": None,
        "website_quality": None,
        "pain_points": [],
        "business_level": None,
        "marketing_signals": {
            "has_ads": None,
            "has_socials": None,
            "has_blog": None,
        },
        "location": None,
        "language": None,
        "contact": None,
        "sales_hook": None,
        "tech_stack": {"cms": "unknown"},
        "performance": {
            "load_speed": None,
            "mobile_friendly": None,
            "has_cta": None,
            "navigation_quality": None,
        },
        "features": {
            "has_online_booking": None,
            "has_forms": None,
            "has_ecommerce": None,
            "multilingual": None,
        },
        "seo": {"has_blog": None, "content_quality": None},
        "technical_pain_points": [],
        "reviews_section": {
            "has_reviews": None,
            "reviews_text": None,
        },
    }


def _should_ignore_link(url: str) -> bool:
    path = (urlsplit(url).path or "").strip().lower()
    if any(path == p or path.startswith(p + "/") for p in _IGNORED_PATH_PREFIXES):
        return True
    legal_path_markers = (
        "/terms",
        "/conditions",
        "/gdpr",
        "/policy",
        "/legal",
        "/politika-cookies",
        "/cookies",
        "/cookie",
        "/registrace",
        "/zapomenute-heslo",
        "/souhlas-se-zpracovanim-osobnich-udaju-pri-objednavce",
        "/souhlas-se-zpracovanim-osobnich-udaju-pri-odeslani-formularu-a-prihlasenim-do-newsletteru",
        "/podminky-ochrany-osobnich-udaju",
        "/podminky-ochrany",
        "/zasady-ochrany-soukromi",
        "/ochrana-soukromi",
        "/ochrana-osobnich-udaju",
        "/obchodni-podminky",
        "/privacy-policy",
    )
    return any(m in path for m in legal_path_markers)


def _normalize_host(host: str) -> str:
    h = (host or "").strip().lower()
    return h[4:] if h.startswith("www.") else h


def _is_internal_link(link_url: str, base_url: str) -> bool:
    return _normalize_host(urlsplit(link_url).netloc) == _normalize_host(urlsplit(base_url).netloc)


def _keep_internal_links(links: Iterable[str], base_url: str) -> list[str]:
    out: list[str] = []
    for link in links:
        s = (link or "").strip()
        if not s:
            continue
        if _is_internal_link(s, base_url):
            out.append(s)
    return _dedupe_keep_order(out)


def _priority_rank(url: str) -> int:
    path = (urlsplit(url).path or "").strip().lower()
    for idx, prefix in enumerate(_PRIORITY_PATH_PREFIXES):
        if path == prefix or path.startswith(prefix + "/"):
            return idx
    return len(_PRIORITY_PATH_PREFIXES) + 1


def _normalize_url(href: str, base_url: str) -> str:
    if not href or href.startswith(("mailto:", "tel:", "javascript:", "#")):
        return ""
    absolute = urljoin(base_url, href)
    parts = urlsplit(absolute)
    if parts.scheme not in ("http", "https"):
        return ""
    if not _is_internal_link(absolute, base_url):
        return ""
    if _should_ignore_link(absolute):
        return ""
    return urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ""))


def _dedupe_keep_order(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for v in values:
        if not v or v in seen:
            continue
        seen.add(v)
        out.append(v)
    return out


def _contains_any(haystack: str, needles: Iterable[str]) -> bool:
    h = (haystack or "").lower()
    return any(n in h for n in needles)


def _norm_text_key(text: str) -> str:
    s = re.sub(r"\s+", " ", (text or "").strip().lower())
    return re.sub(r"[^\w\s]+", "", s)


def _is_boilerplate_chunk(text: str) -> bool:
    t = _norm_text_key(text)
    if not t:
        return True
    generic_markers = (
        "cookies",
        "cookie",
        "gdpr",
        "privacy",
        "consent",
        "souhlasim",
        "souhlasit",
        "prijmout",
        "accept all",
    )
    strong_markers = (
        "pouzivame soubory cookies",
        "soubory cookies",
        "za ucelem zlepseni vam poskytovanych sluzeb",
        "ochrana osobnich udaju",
        "zasady ochrany soukromi",
        "privacy policy",
    )
    # Remove only clearly-consent chunks.
    generic_hits = sum(1 for m in generic_markers if m in t)
    strong_hit = any(m in t for m in strong_markers)
    if (strong_hit and len(t) < 700) or (generic_hits >= 2 and len(t) < 380):
        return True
    return False


def _dedupe_repeated_long_chunks(text: str, *, min_chars: int = 180) -> str:
    raw = (text or "").strip()
    if not raw:
        return ""
    chunks = [c.strip() for c in re.split(r"\n\s*\n+", raw) if c.strip()]
    seen: set[str] = set()
    out: list[str] = []
    for chunk in chunks:
        if _is_boilerplate_chunk(chunk):
            continue
        key = _norm_text_key(chunk)
        if len(key) >= min_chars:
            if key in seen:
                continue
            seen.add(key)
        out.append(chunk)
    # Fallback: if boilerplate filter removed everything, keep content with dedupe only.
    if not out:
        seen2: set[str] = set()
        out2: list[str] = []
        for chunk in chunks:
            key = _norm_text_key(chunk)
            if len(key) >= min_chars:
                if key in seen2:
                    continue
                seen2.add(key)
            out2.append(chunk)
        return "\n\n".join(out2).strip()
    return "\n\n".join(out).strip()


def _is_review_chunk(text: str) -> bool:
    t = _norm_text_key(text)
    if not t:
        return False
    review_markers = (
        "recenze",
        "hodnoceni",
        "hodnocení",
        "reviews",
        "review",
        "testimonial",
        "testimonials",
        "reference",
        "references",
        "google reviews",
        "customer review",
        "co o nas rikali",
        "co o nás říkali",
    )
    marker_hits = sum(1 for m in review_markers if _norm_text_key(m) in t)
    has_rating = bool(re.search(r"(?:\b[1-5](?:[.,]\d)?\s*/\s*5\b|★★★★★|⭐{3,})", text or "", re.I))
    # Conservative rule: classify as review only with strong evidence.
    return marker_hits >= 2 or (marker_hits >= 1 and has_rating)


def _split_reviews_from_text(text: str) -> tuple[str, str, bool]:
    chunks = [c.strip() for c in re.split(r"\n\s*\n+", (text or "").strip()) if c.strip()]
    if not chunks:
        return "", "", False
    content_chunks: list[str] = []
    review_chunks: list[str] = []
    for chunk in chunks:
        if _is_review_chunk(chunk):
            review_chunks.append(chunk)
        else:
            content_chunks.append(chunk)
    content_text = "\n\n".join(content_chunks).strip()
    reviews_text = "\n\n".join(review_chunks).strip()
    return content_text, reviews_text, bool(review_chunks)


def _first_non_empty(*values):
    for v in values:
        if v is None:
            continue
        if isinstance(v, str) and not v.strip():
            continue
        if isinstance(v, list) and not v:
            continue
        return v
    return None


def _detect_language(raw_html: str, text: str) -> Optional[str]:
    m = re.search(r"""<html[^>]*\blang=["']?([a-zA-Z\-]+)""", raw_html or "", re.I)
    if m:
        return m.group(1).lower()
    t = (text or "").lower()
    if _contains_any(t, ("č", "ř", "š", "ž", "ě", "í", "ý", "ů")):
        return "cs"
    if _contains_any(t, ("ы", "э", "ъ", "ё", "услуги", "компания")):
        return "ru"
    if _contains_any(t, (" the ", " and ", " services ")):
        return "en"
    return None


def _extract_contact(raw_html: str, text: str) -> Optional[str]:
    blob = "{}\n{}".format(raw_html or "", text or "")
    email = None
    em = _EMAIL_RE.search(blob)
    if em:
        email = em.group(0).strip().lower()
    phone = None
    ph = _PHONE_RE.search(blob)
    if ph:
        phone = re.sub(r"\s+", " ", ph.group(0)).strip()
    if email and phone:
        return "{} | {}".format(email, phone)
    return email or phone


def _assess_profile(url: str, raw_html: str, text: str, all_links: list[str], filtered_links: list[str]) -> dict:
    html_low = (raw_html or "").lower()
    text_low = (text or "").lower()
    profile = _empty_profile()

    has_viewport = ('name="viewport"' in html_low) or ("name='viewport'" in html_low)
    has_forms = "<form" in html_low
    has_cta = _contains_any(text_low + "\n" + html_low, ("contact us", "request demo", "book", "call now", "kontakt", "rezerv", "objednat", "связаться", "заявк", "демо"))
    has_online_booking = _contains_any(text_low + "\n" + html_low, ("booking", "reservation", "rezervace", "appointment", "objednat termin"))
    has_ecommerce = _contains_any(text_low + "\n" + html_low, ("add to cart", "checkout", "basket", "košík", "kosik", "e-shop", "eshop"))
    multilingual = "hreflang=" in html_low or _contains_any(html_low, ("lang=\"en", "lang='en", "switch language", "jazyk"))

    has_blog = any("/blog" in (u or "").lower() for u in all_links) or ("/blog" in html_low)
    nav_count = len(filtered_links)
    nav_quality = "good" if nav_count >= 8 else ("average" if nav_count >= 3 else "poor")

    html_size = len((raw_html or "").encode("utf-8", errors="ignore"))
    script_count = html_low.count("<script")
    load_speed = "slow" if (html_size > 1_500_000 or script_count > 70) else ("average" if (html_size > 600_000 or script_count > 30) else "fast")

    text_len = len((text or "").strip())
    content_quality = "high" if text_len >= 2500 else ("medium" if text_len >= 800 else "low")

    pain_points: list[str] = []
    if not has_viewport:
        pain_points.append("No mobile viewport meta detected.")
    if nav_quality == "poor":
        pain_points.append("Weak internal navigation signals.")
    if not has_cta:
        pain_points.append("No clear CTA detected.")
    if text_len < 500:
        pain_points.append("Low amount of useful textual content.")
    if not has_forms and not has_online_booking:
        pain_points.append("No lead capture forms or booking flow detected.")

    cms = "unknown"
    if _contains_any(html_low, ("wp-content", "wordpress")):
        cms = "wordpress"
    elif _contains_any(html_low, ("shopify",)):
        cms = "custom"
    elif _contains_any(html_low, ("wix",)):
        cms = "wix"
    elif _contains_any(html_low, ("webflow",)):
        cms = "custom"
    elif _contains_any(html_low, ("drupal", "joomla", "ghost", "squarespace", "bitrix", "opencart", "prestashop")):
        cms = "custom"
    elif script_count > 0:
        cms = "custom"

    has_ads: Optional[bool] = None
    if _contains_any(html_low, ("doubleclick", "googlesyndication", "adsbygoogle", "googleadservices", "gclid=", "adservice", "fbq(", "pixel")):
        has_ads = True
    elif script_count == 0:
        has_ads = False

    has_socials: Optional[bool] = None
    social_markers = ("facebook.com", "instagram.com", "linkedin.com", "youtube.com", "tiktok.com", "x.com", "twitter.com")
    if any(_contains_any((u or "").lower(), social_markers) for u in all_links):
        has_socials = True
    elif len(all_links) > 0:
        has_socials = False

    if not url:
        website_quality = "low"
    elif content_quality == "high" and nav_quality != "poor":
        website_quality = "high"
    elif content_quality in ("medium", "high"):
        website_quality = "mid"
    else:
        website_quality = "low"

    services: list[str] = []
    for name, keys in (
        ("consulting", ("consulting", "poradenství", "konsultace")),
        ("development", ("development", "vývoj", "разработка")),
        ("design", ("design", "grafika", "ux", "ui", "дизайн")),
        ("marketing", ("marketing", "seo", "ppc", "smm")),
        ("booking", ("rezervace", "booking", "appointment")),
        ("ecommerce", ("e-shop", "eshop", "магазин")),
    ):
        if _contains_any(text_low + "\n" + html_low, keys):
            services.append(name)

    business_type = None
    if _contains_any(text_low, ("agency", "agentura", "агентств")):
        business_type = "agency"
    elif _contains_any(text_low, ("clinic", "ordinace", "léka", "dent", "stomat")):
        business_type = "healthcare"
    elif _contains_any(text_low, ("restaurant", "restaurace", "cafe", "bar")):
        business_type = "hospitality"
    elif has_ecommerce:
        business_type = "ecommerce"
    elif _contains_any(text_low, ("software", "saas", "technolog")):
        business_type = "technology"

    target_audience = None
    if _contains_any(text_low, ("b2b", "firm", "company", "podnik")):
        target_audience = "b2b"
    elif _contains_any(text_low, ("consumer", "b2c", "customers", "zákazník", "zakaznik")):
        target_audience = "b2c"

    business_level = None
    if _contains_any(text_low, ("enterprise", "korpor", "holding")):
        business_level = "enterprise"
    elif _contains_any(text_low, ("small business", "sme", "малый бизнес", "živnost")):
        business_level = "smb"

    sales_hook = None
    m_hook = re.search(r"([^\n]{20,180}(?:free|zdarma|sleva|discount|demo|trial|garance)[^\n]{0,120})", text or "", re.I)
    if m_hook:
        sales_hook = m_hook.group(1).strip()

    location = None
    m_loc = re.search(r"\b(Prague|Praha|Brno|Ostrava|Plzeň|Plzen|Bratislava|Warsaw|Wien|Vienna)\b", text or "", re.I)
    if m_loc:
        location = m_loc.group(1)

    profile.update(
        {
            "business_type": business_type,
            "services": services,
            "target_audience": target_audience,
            "has_website": True if url else None,
            "website_quality": website_quality,
            "pain_points": pain_points[:],
            "business_level": business_level,
            "marketing_signals": {
                "has_ads": has_ads,
                "has_socials": has_socials,
                "has_blog": bool(has_blog),
            },
            "location": location,
            "language": _detect_language(raw_html, text),
            "contact": _extract_contact(raw_html, text),
            "sales_hook": sales_hook,
            "tech_stack": {"cms": cms},
            "performance": {
                "load_speed": load_speed,
                "mobile_friendly": bool(has_viewport),
                "has_cta": bool(has_cta),
                "navigation_quality": nav_quality,
            },
            "features": {
                "has_online_booking": bool(has_online_booking),
                "has_forms": bool(has_forms),
                "has_ecommerce": bool(has_ecommerce),
                "multilingual": bool(multilingual),
            },
            "seo": {"has_blog": bool(has_blog), "content_quality": content_quality},
            "technical_pain_points": pain_points,
        }
    )
    return profile


def merge_parsed_contents(items: list[ParsedContent]) -> dict:
    out = _empty_profile()
    if not items:
        return out
    profiles = [x.profile for x in items if isinstance(x.profile, dict)]
    if not profiles:
        return out

    out["business_type"] = _first_non_empty(*[p.get("business_type") for p in profiles])
    out["target_audience"] = _first_non_empty(*[p.get("target_audience") for p in profiles])
    wq_vals = [p.get("website_quality") for p in profiles if p.get("website_quality")]
    out["website_quality"] = "high" if "high" in wq_vals else ("mid" if "mid" in wq_vals else ("low" if "low" in wq_vals else None))
    out["business_level"] = _first_non_empty(*[p.get("business_level") for p in profiles])
    out["location"] = _first_non_empty(*[p.get("location") for p in profiles])
    out["language"] = _first_non_empty(*[p.get("language") for p in profiles])
    out["contact"] = _first_non_empty(*[p.get("contact") for p in profiles])
    out["sales_hook"] = _first_non_empty(*[p.get("sales_hook") for p in profiles])
    out["has_website"] = any(p.get("has_website") is True for p in profiles)

    services = []
    pain = []
    tech_pain = []
    for p in profiles:
        services.extend(p.get("services") or [])
        pain.extend(p.get("pain_points") or [])
        tech_pain.extend(p.get("technical_pain_points") or [])
    out["services"] = _dedupe_keep_order(services)
    out["pain_points"] = _dedupe_keep_order(pain)
    out["technical_pain_points"] = _dedupe_keep_order(tech_pain)

    perf = out["performance"]
    loads = [p.get("performance", {}).get("load_speed") for p in profiles if p.get("performance", {}).get("load_speed")]
    perf["load_speed"] = "slow" if "slow" in loads else ("average" if "average" in loads else ("fast" if "fast" in loads else None))
    perf["mobile_friendly"] = False if any(p.get("performance", {}).get("mobile_friendly") is False for p in profiles) else (
        True if any(p.get("performance", {}).get("mobile_friendly") is True for p in profiles) else None
    )
    perf["has_cta"] = True if any(p.get("performance", {}).get("has_cta") is True for p in profiles) else (
        False if any(p.get("performance", {}).get("has_cta") is False for p in profiles) else None
    )
    navs = [p.get("performance", {}).get("navigation_quality") for p in profiles if p.get("performance", {}).get("navigation_quality")]
    perf["navigation_quality"] = "good" if "good" in navs else ("average" if "average" in navs else ("poor" if "poor" in navs else None))

    feats = out["features"]
    for k in ("has_online_booking", "has_forms", "has_ecommerce", "multilingual"):
        feats[k] = True if any(p.get("features", {}).get(k) is True for p in profiles) else (
            False if any(p.get("features", {}).get(k) is False for p in profiles) else None
        )

    seo = out["seo"]
    seo["has_blog"] = True if any(p.get("seo", {}).get("has_blog") is True for p in profiles) else (
        False if any(p.get("seo", {}).get("has_blog") is False for p in profiles) else None
    )
    cqs = [p.get("seo", {}).get("content_quality") for p in profiles if p.get("seo", {}).get("content_quality")]
    seo["content_quality"] = "high" if "high" in cqs else ("medium" if "medium" in cqs else ("low" if "low" in cqs else None))

    mkt = out["marketing_signals"]
    for k in ("has_ads", "has_socials", "has_blog"):
        mkt[k] = True if any(p.get("marketing_signals", {}).get(k) is True for p in profiles) else (
            False if any(p.get("marketing_signals", {}).get(k) is False for p in profiles) else None
        )

    stack = out["tech_stack"]
    cms_vals = [p.get("tech_stack", {}).get("cms") for p in profiles if p.get("tech_stack", {}).get("cms")]
    if "wordpress" in cms_vals:
        stack["cms"] = "wordpress"
    elif "wix" in cms_vals:
        stack["cms"] = "wix"
    elif "custom" in cms_vals:
        stack["cms"] = "custom"
    elif "unknown" in cms_vals:
        stack["cms"] = "unknown"
    else:
        stack["cms"] = "unknown"

    reviews = out["reviews_section"]
    has_reviews_vals = [p.get("reviews_section", {}).get("has_reviews") for p in profiles if p.get("reviews_section", {}).get("has_reviews") is not None]
    reviews["has_reviews"] = True if any(v is True for v in has_reviews_vals) else (False if has_reviews_vals else None)
    review_texts: list[str] = []
    for p in profiles:
        rt = (p.get("reviews_section", {}).get("reviews_text") or "").strip()
        if rt:
            review_texts.append(rt)
    merged_reviews = _dedupe_repeated_long_chunks("\n\n".join(review_texts), min_chars=120) if review_texts else ""
    reviews["reviews_text"] = merged_reviews[:8000] if merged_reviews else None
    return out


def _looks_like_cookie_wall(text: str, html: str) -> bool:
    blob = _norm_text_key((text or "") + "\n" + (html or ""))
    if not blob:
        return True
    markers = (
        "cookie",
        "cookies",
        "gdpr",
        "consent",
        "privacy policy",
        "soubory cookies",
        "souhlasim",
        "prijmout",
        "accept all",
    )
    hits = sum(1 for m in markers if m in blob)
    return hits >= 2 and len(_norm_text_key(text or "")) < 800


def _looks_like_legal_policy(text: str, url: str) -> bool:
    t = _norm_text_key(text)
    u = (url or "").lower()
    legal_markers = (
        "gdpr",
        "ochrana osobnich udaju",
        "podminky ochrany osobnich udaju",
        "privacy policy",
        "terms and conditions",
        "obchodni podminky",
        "zpracovani osobnich udaju",
        "spravce osobnich udaju",
        "cl 6 odst",
        "na rizeni eu 2016679",
    )
    marker_hits = sum(1 for m in legal_markers if m in t)
    url_legal = any(x in u for x in ("/privacy", "/gdpr", "/terms", "/conditions", "/policy", "/legal"))
    # Treat as legal page if markers dominate or URL clearly legal.
    return (marker_hits >= 3 and len(t) > 400) or (url_legal and marker_hits >= 1)


def _extract_text_and_links_with_trafilatura(page_html: str, url: str):
    import trafilatura

    # Extract links from raw HTML first so header/nav links are not lost.
    raw_parser = _LinkExtractor()
    raw_parser.feed(page_html or "")
    raw_links = _dedupe_keep_order(_normalize_url(href, url) for href in raw_parser.links)
    raw_links = [x for x in raw_links if x]

    text = trafilatura.extract(
        page_html,
        include_links=False,
        include_tables=False,
        favor_precision=True,
        output_format="txt",
        url=url,
    ) or ""
    text = _dedupe_repeated_long_chunks(text)

    cleaned_html = trafilatura.extract(
        page_html,
        include_links=True,
        include_tables=False,
        favor_precision=True,
        output_format="html",
        url=url,
    ) or ""

    parser = _LinkExtractor()
    parser.feed(cleaned_html)
    cleaned_links = _dedupe_keep_order(_normalize_url(href, url) for href in parser.links)
    cleaned_links = [x for x in cleaned_links if x]
    # Merge cleaned and raw links; raw keeps header/navigation URLs that trafilatura can remove.
    links = _dedupe_keep_order(list(cleaned_links) + list(raw_links))
    if not links:
        links = list(raw_links)
    links = [
        link
        for _, _, link in sorted(
            [(_priority_rank(link), idx, link) for idx, link in enumerate(links)],
            key=lambda t: (t[0], t[1]),
        )
    ]
    return text, links


def _fetch_rendered_html_with_playwright(url: str) -> str:
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        return ""

    consent_labels = (
        "Accept all",
        "Accept",
        "I agree",
        "Agree",
        "Souhlasím",
        "Souhlasit",
        "Přijmout vše",
        "Přijmout",
        "Rozumím",
        "OK",
    )

    with sync_playwright() as p:
        browser = p.chromium.launch(**chromium_launch_kwargs())
        page = browser.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=90000)
            page.wait_for_timeout(1800)
            # Try common consent buttons (best-effort).
            for label in consent_labels:
                try:
                    btn = page.get_by_role("button", name=label)
                    if btn.count():
                        btn.first.click(timeout=1200)
                        page.wait_for_timeout(1000)
                        break
                except Exception:
                    continue
            page.wait_for_timeout(1200)
            return page.content() or ""
        except Exception:
            return ""
        finally:
            browser.close()


def parse_url_to_content(url: str, _depth: int = 0) -> ParsedContent:
    try:
        import trafilatura
    except ImportError as e:
        raise RuntimeError("Missing dependency 'trafilatura'. Install with: pip install trafilatura") from e

    downloaded = trafilatura.fetch_url(url)
    if not downloaded:
        raise RuntimeError("Could not download page content: {}".format(url))

    text, links = _extract_text_and_links_with_trafilatura(downloaded, url)
    effective_html = downloaded

    # If we likely parsed only consent wall, render page in browser and retry extraction.
    if _looks_like_cookie_wall(text, downloaded):
        rendered = _fetch_rendered_html_with_playwright(url)
        if rendered:
            alt_text, alt_links = _extract_text_and_links_with_trafilatura(rendered, url)
            if len((alt_text or "").strip()) > len((text or "").strip()):
                text, links = alt_text, alt_links
                effective_html = rendered

    raw_link_parser = _LinkExtractor()
    raw_link_parser.feed(effective_html or "")
    all_links = _dedupe_keep_order(_normalize_url(href, url) for href in raw_link_parser.links)
    all_links = [x for x in all_links if x]
    all_links = _keep_internal_links(all_links, url)
    # If filtered links are empty after content extraction, fallback to raw internal links.
    if not links and all_links:
        links = [
            link
            for _, _, link in sorted(
                [(_priority_rank(link), idx, link) for idx, link in enumerate(all_links)],
                key=lambda t: (t[0], t[1]),
            )
        ]

    # Second-level rescue: if text is banner-like or legal-policy-like, try internal business links.
    if (_looks_like_cookie_wall(text, effective_html or "") or _looks_like_legal_policy(text, url)) and _depth < 1:
        sorted_candidates = sorted(
            [lnk for lnk in all_links if lnk and lnk != url],
            key=lambda x: (_priority_rank(x), len(urlsplit(x).path or "")),
        )
        candidate_links = []
        for lnk in sorted_candidates:
            if _should_ignore_link(lnk):
                continue
            candidate_links.append(lnk)
            if len(candidate_links) >= 5:
                break
        best_text = text or ""
        best_links = links
        best_profile_html = effective_html
        for lnk in candidate_links:
            try:
                nested = parse_url_to_content(lnk, _depth=_depth + 1)
            except Exception:
                continue
            nt = (nested.text or "").strip()
            if (
                len(nt) > len((best_text or "").strip())
                and not _looks_like_cookie_wall(nt, "")
                and not _looks_like_legal_policy(nt, lnk)
            ):
                best_text = nt
                best_links = nested.links or best_links
                best_profile_html = effective_html
        text = best_text
        links = best_links
        effective_html = best_profile_html

    # Final safety filter: never return external links.
    links = _keep_internal_links(links or [], url)
    all_links = _keep_internal_links(all_links or [], url)

    core_text, reviews_text, has_reviews = _split_reviews_from_text(text)
    # Technical/quality heuristics must use non-review content only.
    profile = _assess_profile(url, effective_html or "", core_text, all_links, links)
    profile["reviews_section"] = {
        "has_reviews": has_reviews,
        "reviews_text": (reviews_text[:8000] if reviews_text else None),
    }
    return ParsedContent(source_url=url, text=core_text.strip(), links=links, profile=profile)


if __name__ == "__main__":
    import json
    import sys

    if len(sys.argv) < 2:
        raise SystemExit("Usage: python -m tools.universal_content_parser <url>")
    result = parse_url_to_content(sys.argv[1].strip())
    print(json.dumps(result.to_json_dict(), ensure_ascii=False, indent=2))
