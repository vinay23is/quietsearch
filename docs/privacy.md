# Privacy model

Quietsearch **reduces** how much individual search providers learn about the
person searching. It does **not** provide anonymity. This document is the
technical version of the instance's user-facing privacy page
(`reverse-proxy/site/privacy.html`, served at `/instance/privacy`).

## What changes compared with searching directly

| Searching a provider directly | Searching through Quietsearch |
|---|---|
| The provider sees your IP, browser fingerprint, cookies and account | The provider sees the server's IP and a generic, generated browser User-Agent |
| The provider ties your searches to your browser identity and account | Every enabled engine in a category receives each query in that category (e.g. all three web engines see every web search), but none can tie it to your IP, cookies or account. Searches in other categories (IT, science) go to different engines entirely |
| Ads, tracking scripts and click-tracking redirects on the results page | Plain results page served by this instance; tracking parameters removed from result URLs |
| Partial queries sent as you type (autocomplete) | Autocomplete off by default |

## What each party can still see

| Party | Visibility |
|---|---|
| **Upstream engines** | The full query text, sent from the server's IP. With a single-user instance, that IP effectively identifies one person, so an engine can still link the queries it receives into a profile of "this server". What it loses is the link to your real IP, cookies, accounts and browser fingerprint. |
| **Cloud / hosting provider** | Network metadata: which engine hosts the server contacts, when, and how much data. Traffic between your browser and the server is TLS-encrypted; outbound requests to engines are HTTPS too, so query text isn't visible on the wire. A hosting provider with access to the machine could in principle read memory or disks. |
| **Instance operator** | Anything the server could log. Mitigation: no proxy access log, SearXNG logs warnings/errors only, no accounts. On a shared instance, every user would have to trust the operator. |
| **DNS providers** | Your resolver sees lookups for the instance's hostname (reveals that you use it, not what you search). The server's resolver sees lookups for engine hostnames. |
| **Your ISP / local network** | Connections to the server's IP and the hostname (TLS SNI), timing and volume. Not query text. |
| **Websites opened from results** | A direct visit: your IP, browser, cookies. `Referrer-Policy: no-referrer` hides that you came from a search page. Thumbnails and image previews go through `/image_proxy`, but following a link is direct. |
| **Domain registrar / WHOIS** | Registrant details, unless WHOIS privacy is enabled. |

## Single-user instance vs public instance

A public SearXNG instance shared by many people gives better *unlinkability*:
an engine sees one IP sending a mix of everyone's queries. A personal
instance gives **control and auditability** instead: you choose the engines,
the logging, and the hosting, and you can verify the configuration. The cost
is that the server IP is effectively yours. Both are legitimate choices; this
project deliberately chooses the second and states it.

## Data stored by the system

| Data | Where | Retention |
|---|---|---|
| Preferences | Cookie in the user's browser | Until the user clears it |
| Search queries | Nowhere: no access log, no history | Not stored |
| Rate-limit counters (client IP) | Caddy process memory | Sliding 1-minute window |
| Engine tokens and cache | `searxng-cache` volume | Short-lived, not tied to a user |
| Container logs (errors/warnings) | Docker `json-file` logs | Rotated: 10 MB × 3 per container |
| TLS certificates / ACME account | `caddy-data` volume | Until the volume is deleted |

## Out of scope

Anonymity against the hosting provider or a network-level adversary, hiding
the IP from visited websites, and protection from malicious result pages.
Users who need those should add a VPN or Tor; SearXNG can also send its own
outbound requests through a proxy or Tor (`outgoing.proxies`), which this
project does not enable.
