import random
import re
import time
from urllib.parse import parse_qsl, quote_plus, urlencode, urljoin, urlparse, urlunparse

# Критерии поиска на Firmy.cz (потребительский интерфейс, см. https://www.firmy.cz ):
# — полнотекстовый запрос (q): название, вид деятельности, локация и т.д.;
# — уточнение локации, фильтры (категории, «Nyní otevřeno» / открыто сейчас), карта;
# — сортировка выдачи на стороне сервиса (по умолчанию релевантность; в API встречаются
#   режимы вроде relevance, distance, top — конкретный набор зависит от ответа сервера).
# Отдельного поля «поиск только по дате добавления записи» в духе календарного диапазона
# в базовом URL OpenSearch нет. Ленту новых/изменённых профилей ведёт RSS:
# https://www.firmy.cz/rss.xml («nové a upravené firmy»).

# Как в OpenSearch Firmy.cz: ?q={query}&sourceid=Searchmodule_1
FIRMY_BASE = "https://www.firmy.cz/"
SOURCE_ID = "Searchmodule_1"

_EMAIL_RE = re.compile(
    r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b",
    re.I,
)

# Чехия: +420, 00420, местные с ведущей 0 (9 цифр после кода)
_PHONE_PATTERNS = [
    re.compile(r"\+420[\s\-]?\d{3}[\s\-]?\d{3}[\s\-]?\d{3}"),
    re.compile(r"00420[\s\-]?\d{3}[\s\-]?\d{3}[\s\-]?\d{3}"),
    re.compile(r"\b420[\s\-]?\d{3}[\s\-]?\d{3}[\s\-]?\d{3}\b"),
    re.compile(r"\b0\d{2}[\s\-]?\d{3}[\s\-]?\d{3}[\s\-]?\d{3}\b"),
]


def random_delay():
    time.sleep(random.uniform(0.5, 1.5))


def _strip_tracking_params(url: str) -> str:
    if not url or not url.startswith("http"):
        return url or ""
    try:
        p = urlparse(url)
        q = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True) if not k.lower().startswith("utm_")]
        new_query = urlencode(q)
        return urlunparse((p.scheme, p.netloc, p.path, p.params, new_query, ""))
    except Exception:
        return url


def _dismiss_seznam_consent(page):
    """Баннер cookies Seznam — без принятия в headless часто не подгружается контент страницы."""
    labels = (
        "Souhlasím",
        "Souhlasit",
        "Souhlasím se vším",
        "Přijmout vše",
        "Přijmout",
        "Rozumím",
        "OK",
        "Accept all",
        "Agree",
    )
    for name in labels:
        try:
            btn = page.get_by_role("button", name=name)
            if btn.count():
                btn.first.click(timeout=3000)
                page.wait_for_timeout(1500)
                return True
        except Exception:
            continue
    return False


def build_search_url(query: str) -> str:
    return "{base}?q={q}&sourceid={sid}".format(
        base=FIRMY_BASE.rstrip("/") + "/",
        q=quote_plus(query.strip()),
        sid=SOURCE_ID,
    )


def _parse_card(card: str):
    lines = [ln.strip() for ln in (card or "").splitlines() if ln.strip()]
    category = lines[1] if len(lines) > 1 else ""
    address = lines[2] if len(lines) > 2 else ""
    return category, address


def _digits_only(s: str) -> str:
    return re.sub(r"\D", "", s or "")


def _normalize_phone(raw: str) -> str:
    s = (raw or "").strip()
    if not s:
        return ""
    d = _digits_only(s)
    if not d:
        return ""
    if len(d) == 12 and d.startswith("420"):
        return "+" + d
    if d.startswith("00420"):
        rest = d[5:]
        if len(rest) == 9:
            return "+420" + rest
    if len(d) == 10 and d.startswith("0"):
        return "+420" + d[1:]
    if len(d) == 9:
        return "+420" + d
    if s.startswith("+"):
        return "+" + d.lstrip("+")
    return s


def _extract_emails_from_text(text: str):
    out = set()
    if not text:
        return out
    for m in _EMAIL_RE.finditer(text):
        e = m.group(0).strip().lower()
        if ".." not in e and e.count("@") == 1:
            out.add(e)
    return out


def _extract_phones_from_text(text: str):
    out = set()
    if not text:
        return out
    for pat in _PHONE_PATTERNS:
        for m in pat.finditer(text):
            n = _normalize_phone(m.group(0))
            if n and len(_digits_only(n)) >= 9:
                out.add(n)
    return out


def _merge_contacts(phones, emails, *text_blobs):
    p = set(phones)
    e = set(emails)
    for blob in text_blobs:
        if not blob:
            continue
        p |= _extract_phones_from_text(blob)
        e |= _extract_emails_from_text(blob)
    return sorted(p), sorted(e)


def _detail_page_data(page):
    """tel:/mailto:, regex по тексту, ссылка «Web» (официальный сайт / часто соцсеть)."""
    data = page.evaluate(
        """
        () => {
          const phones = new Set();
          const emails = new Set();
          document.querySelectorAll('a[href^="tel:"]').forEach(a => {
            let u = (a.getAttribute('href') || '').replace(/^tel:/i, '').trim();
            u = decodeURIComponent(u.split(';')[0].split(',')[0]).trim();
            if (u) phones.add(u);
          });
          document.querySelectorAll('a[href^="mailto:"]').forEach(a => {
            let u = (a.getAttribute('href') || '').replace(/^mailto:/i, '').trim();
            u = decodeURIComponent(u.split('?')[0]).trim();
            if (u) emails.add(u);
          });
          let website = null;
          // Strict selector from Firmy detail page for official website field.
          // Example:
          // <a class="value detailWebUrl url companyUrl" href="https://...">...</a>
          const webAnchor = document.querySelector('a.detailWebUrl.companyUrl[href], a.value.detailWebUrl.url.companyUrl[href]');
          if (webAnchor) {
            const h = (webAnchor.getAttribute('href') || '').trim();
            if (h && /^https?:\\/\\//i.test(h)) website = h;
          }
          const body = document.body ? (document.body.innerText || '') : '';
          return {
            phones: Array.from(phones),
            emails: Array.from(emails),
            body: body.slice(0, 80000),
            website: website
          };
        }
        """
    )
    raw_phones = data.get("phones") or []
    raw_emails = data.get("emails") or []
    body = data.get("body") or ""
    website = (data.get("website") or "").strip()
    p = {_normalize_phone(x) for x in raw_phones if x}
    p.discard("")
    e = {(x or "").strip().lower() for x in raw_emails if x}
    e.discard("")
    phones, emails = _merge_contacts(p, e, body)
    web = _strip_tracking_params(website) if website else ""
    if len(web) > 2048:
        web = web[:2048]
    return phones, emails, web


def fetch_listings(query: str, limit: int):
    """
    Открывает firmy.cz с тем же query, что и поле поиска на сайте, и собирает карточки.
    Для каждой карточки открывает страницу профиля и собирает телефоны и e-mail.
    Между сетевыми шагами — случайная пауза 0.5–1.5 с.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise RuntimeError(
            "Нужен пакет playwright. Установите: pip install playwright && playwright install chromium"
        ) from e

    if limit < 1:
        limit = 1
    if limit > 20:
        limit = 20

    url = build_search_url(query)
    random_delay()

    listings = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=90000)
            random_delay()
            page.wait_for_timeout(2500)
            _dismiss_seznam_consent(page)
            page.wait_for_timeout(2000)

            for _ in range(8):
                batch = page.evaluate(
                    """
                    () => {
                      const out = [];
                      const seen = new Set();
                      document.querySelectorAll('a[href*="/detail/"]').forEach(a => {
                        const m = a.href.match(/\\/detail\\/(\\d+)/);
                        if (!m || seen.has(m[1])) return;
                        seen.add(m[1]);
                        let card = a.closest('article') || a.closest('li') || a.parentElement;
                        if (card && card !== a) {
                          for (let i = 0; i < 4 && card; i++) {
                            const t = (card.innerText || '').trim();
                            if (t.length > 40) break;
                            card = card.parentElement;
                          }
                        }
                        const cardText = card ? (card.innerText || '').trim().slice(0, 4000) : '';
                        out.push({
                          href: a.href,
                          title: (a.innerText || '').trim().slice(0, 500),
                          card: cardText
                        });
                      });
                      return out;
                    }
                    """
                )
                listings = []
                seen_ids = set()
                for row in batch:
                    m = re.search(r"/detail/(\d+)", row.get("href") or "")
                    if not m:
                        continue
                    pid = int(m.group(1))
                    if pid in seen_ids:
                        continue
                    seen_ids.add(pid)
                    listings.append(row)
                    if len(listings) >= limit:
                        break

                if len(listings) >= limit:
                    break
                btn = page.get_by_role("button", name="Zobrazit další")
                if btn.count() == 0:
                    break
                random_delay()
                btn.first.scroll_into_view_if_needed()
                btn.first.click()
                page.wait_for_timeout(5000)

            sliced = listings[:limit]
            enriched = []
            for row in sliced:
                href = row.get("href") or ""
                if not href.startswith("http"):
                    href = urljoin(FIRMY_BASE, href)
                card = row.get("card") or ""
                phones, emails = _merge_contacts(set(), set(), card)

                random_delay()
                website_url = ""
                try:
                    page.goto(href, wait_until="domcontentloaded", timeout=60000)
                    random_delay()
                    page.wait_for_timeout(2000)
                    _dismiss_seznam_consent(page)
                    page.wait_for_timeout(1500)
                    dp, de, website_url = _detail_page_data(page)
                    phones, emails = _merge_contacts(set(phones), set(emails), card, "\n".join(dp), "\n".join(de))
                except Exception:
                    pass

                enriched.append(
                    {
                        "row": row,
                        "href": href,
                        "phones": phones,
                        "emails": emails,
                        "website_url": website_url or "",
                    }
                )
        finally:
            browser.close()

    random_delay()

    out = []
    for i, item in enumerate(enriched, start=1):
        row = item["row"]
        href = item["href"]
        m = re.search(r"/detail/(\d+)", href)
        if not m:
            continue
        card = row.get("card") or ""
        category, address = _parse_card(card)
        phones = item.get("phones") or []
        emails = item.get("emails") or []
        web = (item.get("website_url") or "").strip()[:2048]
        out.append(
            {
                "position": i,
                "premise_id": int(m.group(1)),
                "title": (row.get("title") or "").strip() or href,
                "detail_url": href,
                "category": category,
                "address": address,
                "card_text": card,
                "phones": "\n".join(phones),
                "emails": "\n".join(emails),
                "website_url": web,
            }
        )

    return out

