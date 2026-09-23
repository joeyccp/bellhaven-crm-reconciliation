from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from .config import BASE_URL
from .normalize import clean

MAX_RESPONSE_BYTES = 2_000_000


def _get_html(session: requests.Session, url: str, **kwargs) -> requests.Response:
    response = session.get(url, timeout=20, allow_redirects=False, **kwargs)
    if 300 <= response.status_code < 400:
        raise ValueError(f"Source redirected unexpectedly ({response.status_code}); enter its final public HTTPS base URL.")
    response.raise_for_status()
    if len(response.content) > MAX_RESPONSE_BYTES:
        raise ValueError(f"Source page exceeds the {MAX_RESPONSE_BYTES:,}-byte demo limit.")
    return response


@dataclass(frozen=True)
class Community:
    name: str
    street: str
    city: str
    state: str
    zip: str
    care_offerings: list[str]
    administrator: str
    phone: str
    source_url: str
    discovery_method: str = "operator directory"

    def to_dict(self) -> dict:
        return asdict(self)


def _detail_map(soup: BeautifulSoup) -> dict[str, object]:
    result: dict[str, object] = {}
    for term in soup.select("dl.detail dt"):
        detail = term.find_next_sibling("dd")
        if detail:
            result[term.get_text(" ", strip=True).lower()] = detail
    return result


def parse_community(html: str, source_url: str, discovery_method: str = "operator directory") -> Community:
    soup = BeautifulSoup(html, "html.parser")
    details = _detail_map(soup)
    address = details["address"].get_text("\n", strip=True).splitlines()
    if len(address) != 2:
        raise ValueError(f"Unexpected address format at {source_url}: {address}")
    match = re.fullmatch(r"(.+),\s*([A-Z]{2})\s+(\d{5}(?:-\d{4})?)", address[1])
    if not match:
        raise ValueError(f"Unexpected city/state/zip at {source_url}: {address[1]}")
    offerings = [x.get_text(" ", strip=True) for x in details["care offerings"].select(".badge")]
    if not offerings:
        offerings = [details["care offerings"].get_text(" ", strip=True)]
    return Community(
        name=soup.select_one("h1").get_text(" ", strip=True),
        street=address[0], city=match.group(1), state=match.group(2), zip=match.group(3)[:5],
        care_offerings=offerings,
        administrator=details["administrator"].get_text(" ", strip=True),
        phone=details["phone"].get_text(" ", strip=True), source_url=source_url,
        discovery_method=discovery_method,
    )


def candidate_names_for_parent(accounts: list[dict], parent_name: str) -> list[str]:
    stop = {"senior", "living", "communities", "community", "healthcare", "health", "care",
            "group", "partners", "company", "operator", "parent", "account"}
    brand_tokens = {token for token in clean(parent_name).split() if token not in stop and len(token) > 3}
    if not brand_tokens:
        return []
    return sorted({
        account.get("name", "") for account in accounts
        if brand_tokens.intersection(clean(account.get("name")).split())
        and "Parent Account" not in account.get("name", "")
    })


def _slug(value: str) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", clean(value))).strip("-")


def scrape_communities(session: requests.Session | None = None, base_url: str = BASE_URL,
                       candidate_names: list[str] | None = None) -> list[Community]:
    session = session or requests.Session()
    base_url = base_url.rstrip("/")
    links: set[str] = set()
    page = 1
    while True:
        response = _get_html(session, f"{base_url}/communities", params={"page": page})
        soup = BeautifulSoup(response.text, "html.parser")
        page_links = {
            urljoin(base_url, a["href"])
            for a in soup.select('a[href^="/communities/"]')
        }
        links.update(page_links)
        next_link = soup.find("a", string=lambda s: bool(s and "Next" in s))
        if not next_link:
            break
        page += 1
        if page > 20:
            raise ValueError("Source has more than 20 community pages; stop and review the adapter before continuing")
    if not links:
        raise ValueError("No facility links matching /communities/... were found. This demo supports the assessment site structure only.")
    communities = []
    for url in sorted(links):
        response = _get_html(session, url)
        communities.append(parse_community(response.text, url))
    # Some operator sites have valid facility detail pages that are accidentally
    # omitted from their directory pagination. Probe only CRM names associated
    # with the selected operator brand, require a real 200 detail page, and mark
    # the discovery method so reviewers can see that the directory did not link it.
    for name in candidate_names or []:
        url = f"{base_url}/communities/{_slug(name)}"
        if url in links:
            continue
        response = session.get(url, timeout=20, allow_redirects=False)
        if response.status_code == 404:
            continue
        if 300 <= response.status_code < 400:
            continue
        response.raise_for_status()
        if len(response.content) > MAX_RESPONSE_BYTES:
            continue
        try:
            item = parse_community(response.text, url, "CRM-assisted URL discovery")
        except (AttributeError, KeyError, ValueError):
            continue
        if not any(existing.source_url == item.source_url for existing in communities):
            communities.append(item)
    return communities
