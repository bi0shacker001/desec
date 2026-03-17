# core/api.py
#
# All HTTP calls to the deSEC REST API.
#
# deSEC (https://desec.io) is a free, privacy-respecting DNS hosting service.
# It exposes a REST API documented at https://desec.readthedocs.io/
#
# This module covers:
#   - Token management  (list, create, delete)
#   - Policy management (per-token RRset access rules)
#   - Domain management (list, create, delete)
#   - DNS record (RRset) management (list, create, update, delete, upsert)
#   - Provisioning wizards (DDNS key, single-domain cert key, multi-domain cert key)
#
# IMPORTANT: Every function in this file is synchronous (blocking).  They use
# httpx.get/post/etc. which waits for the HTTP response before returning.
# In the TUI, these are called inside asyncio.get_event_loop().run_in_executor()
# so they don't freeze the UI.  In CLI scripts, blocking is fine.
#
# IF AN API CALL IS FAILING:
#   - 401 Unauthorized  → the token is wrong or expired; check desec.env
#   - 403 Forbidden     → the token lacks the required permission
#   - 404 Not Found     → the domain/token/record doesn't exist
#   - 400 Bad Request   → the request payload is malformed; check the error body
#   - httpx raises HTTPStatusError for 4xx/5xx — catch it in the caller
#
# DEPENDENCIES:
#   httpx — async-capable HTTP client.  Install: pip install httpx
#   core.env — for get_api_base() which reads DESEC_API_BASE from desec.env

from __future__ import annotations

import httpx          # HTTP client library — handles all network requests

from .env import load_env   # reads DESEC_API_BASE from the config file


# ──────────────────────────────────────────────────────────────────────────────
# Request helpers
# ──────────────────────────────────────────────────────────────────────────────

def get_api_base() -> str:
    """
    Return the deSEC API base URL, stripping any trailing slash.

    Reads DESEC_API_BASE from the env file, defaulting to the public deSEC API.
    You'd only change this if you run a self-hosted deSEC instance.

    Returns:
      str — URL like "https://desec.io/api/v1"
    """
    return load_env().get("DESEC_API_BASE", "https://desec.io/api/v1").rstrip("/")


def api_headers(token: str) -> dict:
    """
    Build the HTTP headers required for every authenticated deSEC API call.

    deSEC uses token-based authentication.  The token goes in the Authorization
    header as "Token <value>".  Content-Type must be application/json because
    we send JSON bodies for POST/PATCH requests.

    Parameters:
      token — the deSEC API token string (e.g. "abc123xyz...")

    Returns:
      dict of headers to pass to every httpx request
    """
    return {
        "Authorization": f"Token {token}",
        "Content-Type": "application/json",
    }


# ──────────────────────────────────────────────────────────────────────────────
# Token management
# ──────────────────────────────────────────────────────────────────────────────

def list_tokens(token: str) -> list:
    """
    Fetch all API tokens visible to the given master token.

    Only tokens belonging to the authenticated account are returned.
    Requires: perm_manage_tokens on the master token.

    Parameters:
      token — master API token

    Returns:
      list of token dicts, each containing: id, name, perm_manage_tokens,
      perm_create_domain, perm_delete_domain, allowed_subnets, policies, etc.

    Raises:
      httpx.HTTPStatusError — if the server returns 4xx/5xx
    """
    r = httpx.get(
        f"{get_api_base()}/auth/tokens/",
        headers=api_headers(token),
        timeout=10,  # seconds — avoids hanging forever if the server is unreachable
    )
    r.raise_for_status()  # raises HTTPStatusError for 4xx/5xx responses
    return r.json()


def create_token(
    token: str,
    name: str,
    perm_manage_tokens: bool,
    perm_create_domain: bool,
    perm_delete_domain: bool,
    allowed_subnets: list[str],
    max_unused_period: str | None,
    auto_policy: bool,
) -> dict:
    """
    Create a new API token on the deSEC account.

    The returned dict contains a one-time "token" field — the secret value.
    deSEC shows the secret ONCE; it cannot be retrieved again.  The caller
    must show it to the user immediately.

    Parameters:
      token              — master token used to authenticate this request
      name               — human-readable label for the new token
      perm_manage_tokens — if True, this token can create/delete other tokens
      perm_create_domain — if True, this token can register new domains
      perm_delete_domain — if True, this token can delete domains
      allowed_subnets    — list of CIDR ranges allowed to use this token
                           (empty list = any IP address is allowed)
      max_unused_period  — ISO 8601 duration after which the token is auto-revoked
                           if unused (e.g. "P90D" = 90 days).  None = never expires.
      auto_policy        — if True, deSEC automatically creates a permissive policy
                           when a new domain is created with this token

    Returns:
      dict containing at minimum: id, name, token (one-time secret)

    Raises:
      httpx.HTTPStatusError — on API error
    """
    # Build the request payload.  Only include optional fields if they have values.
    payload: dict = {
        "name": name,
        "perm_manage_tokens": perm_manage_tokens,
        "perm_create_domain": perm_create_domain,
        "perm_delete_domain": perm_delete_domain,
        "auto_policy": auto_policy,
    }
    # Only include allowed_subnets if there are any — an empty list is valid but
    # we want to match the API's natural default behaviour when not restricting.
    if allowed_subnets:
        payload["allowed_subnets"] = allowed_subnets
    # Only include max_unused_period if it was specified
    if max_unused_period:
        payload["max_unused_period"] = max_unused_period

    r = httpx.post(
        f"{get_api_base()}/auth/tokens/",
        headers=api_headers(token),
        json=payload,
        timeout=10,
    )
    r.raise_for_status()
    return r.json()


def delete_token(token: str, token_id: str) -> None:
    """
    Permanently delete an API token.

    WARNING: This is immediate and irreversible.  Any system using the deleted
    token will start getting 401 Unauthorized errors.

    Parameters:
      token    — master token used to authenticate
      token_id — UUID of the token to delete (from list_tokens()[n]["id"])

    Raises:
      httpx.HTTPStatusError — on API error (e.g. 404 if token not found)
    """
    r = httpx.delete(
        f"{get_api_base()}/auth/tokens/{token_id}/",
        headers=api_headers(token),
        timeout=10,
    )
    r.raise_for_status()


# ──────────────────────────────────────────────────────────────────────────────
# Policy management
# ──────────────────────────────────────────────────────────────────────────────
#
# deSEC policies control which DNS records a token can read or write.
# Each policy entry specifies:
#   domain   — which domain the policy applies to (None = any domain)
#   subname  — which subdomain label (None = any subname)
#   type     — which record type (None = any type)
#   perm_write — True = allow writes; False = read-only (or explicitly deny)
#
# IMPORTANT CONSTRAINT: deSEC requires that a "default" catch-all policy
# (domain=None, subname=None, type=None) exists BEFORE any scoped policies.
# The API will return a 400 "Policy precedence" error if you try to add a
# scoped policy without a default one.  See _do_add_policy in PolicyScreen for
# the auto-fix logic that handles this transparently.


def list_policies(token: str, token_id: str) -> list:
    """
    Fetch all RRset policies for a specific token.

    Parameters:
      token    — master token used to authenticate
      token_id — UUID of the token whose policies to retrieve

    Returns:
      list of policy dicts, each containing: id, domain, subname, type, perm_write

    Raises:
      httpx.HTTPStatusError — on API error
    """
    r = httpx.get(
        f"{get_api_base()}/auth/tokens/{token_id}/policies/rrsets/",
        headers=api_headers(token),
        timeout=10,
    )
    r.raise_for_status()
    return r.json()


def create_policy(
    token: str,
    token_id: str,
    domain: str | None,
    subname: str | None,
    rtype: str | None,
    perm_write: bool,
) -> dict:
    """
    Create a new access policy on a token.

    Pass domain=None, subname=None, rtype=None to create the required default
    (catch-all) policy that must exist before any scoped policies can be added.

    Parameters:
      token      — master token for authentication
      token_id   — UUID of the token to add the policy to
      domain     — domain name (e.g. "example.dedyn.io") or None for catch-all
      subname    — subdomain label (e.g. "www") or None for all subnames
      rtype      — DNS record type (e.g. "A", "TXT") or None for all types
      perm_write — True = write allowed; False = read-only/deny

    Returns:
      dict containing the newly created policy (id, domain, subname, type, perm_write)

    Raises:
      httpx.HTTPStatusError — 400 with "Policy precedence" if no default policy exists yet
    """
    payload = {
        "domain": domain or None,
        "subname": subname or None,
        "type": rtype or None,
        "perm_write": perm_write,
    }
    r = httpx.post(
        f"{get_api_base()}/auth/tokens/{token_id}/policies/rrsets/",
        headers=api_headers(token),
        json=payload,
        timeout=10,
    )
    r.raise_for_status()
    return r.json()


def delete_policy(token: str, token_id: str, policy_id: str) -> None:
    """
    Delete a specific policy from a token.

    Parameters:
      token     — master token for authentication
      token_id  — UUID of the token that owns the policy
      policy_id — UUID of the policy to delete

    Raises:
      httpx.HTTPStatusError — on API error
    """
    r = httpx.delete(
        f"{get_api_base()}/auth/tokens/{token_id}/policies/rrsets/{policy_id}/",
        headers=api_headers(token),
        timeout=10,
    )
    r.raise_for_status()


# ──────────────────────────────────────────────────────────────────────────────
# Domain management
# ──────────────────────────────────────────────────────────────────────────────

def list_domains(token: str) -> list:
    """
    Fetch all domains registered under the account.

    Parameters:
      token — API token with domain read access

    Returns:
      list of domain dicts, each containing: name, created, minimum_ttl, published, etc.

    Raises:
      httpx.HTTPStatusError — on API error
    """
    r = httpx.get(
        f"{get_api_base()}/domains/",
        headers=api_headers(token),
        timeout=10,
    )
    r.raise_for_status()
    return r.json()


def create_domain(token: str, name: str) -> dict:
    """
    Register a new domain with deSEC.

    For dedyn.io subdomains, the name should end in ".dedyn.io".
    For custom domains, the name is the full domain (e.g. "example.com").
    Requires: perm_create_domain on the token.

    Parameters:
      token — API token
      name  — full domain name to register (e.g. "myhost.dedyn.io")

    Returns:
      dict with the newly created domain's info

    Raises:
      httpx.HTTPStatusError — 409 Conflict if domain already exists
    """
    r = httpx.post(
        f"{get_api_base()}/domains/",
        headers=api_headers(token),
        json={"name": name},
        timeout=10,
    )
    r.raise_for_status()
    return r.json()


def delete_domain(token: str, name: str) -> None:
    """
    Delete a domain and ALL its DNS records.

    WARNING: This is immediate and irreversible.  All DNS records under this
    domain will be permanently deleted.
    Requires: perm_delete_domain on the token.

    Parameters:
      token — API token
      name  — domain name to delete

    Raises:
      httpx.HTTPStatusError — on API error
    """
    r = httpx.delete(
        f"{get_api_base()}/domains/{name}/",
        headers=api_headers(token),
        timeout=10,
    )
    r.raise_for_status()


# ──────────────────────────────────────────────────────────────────────────────
# DNS record (RRset) management
# ──────────────────────────────────────────────────────────────────────────────
#
# An "RRset" is a Resource Record Set — all DNS records of the same type at
# the same name.  For example, all A records for "www.example.com" form one
# RRset.  deSEC manages DNS at the RRset level, not individual records.
#
# subname: the part of the hostname before the domain.  E.g. for
#   "www.example.dedyn.io" the domain is "example.dedyn.io" and subname is "www".
#   An empty string ("") or "@" means the apex (root) of the domain.


def list_rrsets(token: str, domain: str) -> list:
    """
    Fetch all RRsets (DNS records) for a specific domain.

    Parameters:
      token  — API token with read access to the domain
      domain — domain name (e.g. "example.dedyn.io")

    Returns:
      list of RRset dicts, each containing: subname, type, ttl, records

    Raises:
      httpx.HTTPStatusError — on API error
    """
    r = httpx.get(
        f"{get_api_base()}/domains/{domain}/rrsets/",
        headers=api_headers(token),
        timeout=10,
    )
    r.raise_for_status()
    return r.json()


def create_rrset(
    token: str,
    domain: str,
    subname: str,
    rtype: str,
    ttl: int,
    records: list[str],
) -> dict:
    """
    Create a new RRset (DNS record set) under a domain.

    Use update_rrset() if the record already exists (or upsert_rrset() if unsure).

    Parameters:
      token   — API token with write access to this domain
      domain  — domain name
      subname — subdomain label (empty string = apex)
      rtype   — DNS record type: "A", "AAAA", "CNAME", "MX", "TXT", etc.
      ttl     — time-to-live in seconds (how long resolvers cache this record)
      records — list of RDATA values (e.g. ["1.2.3.4"] for an A record,
                ["\"v=spf1 ...\""] for TXT — note the quotes in TXT RDATA)

    Returns:
      dict with the created RRset

    Raises:
      httpx.HTTPStatusError — 409 Conflict if RRset already exists
    """
    payload = {
        "subname": subname,
        "type": rtype,
        "ttl": ttl,
        "records": records,
    }
    r = httpx.post(
        f"{get_api_base()}/domains/{domain}/rrsets/",
        headers=api_headers(token),
        json=payload,
        timeout=10,
    )
    r.raise_for_status()
    return r.json()


def update_rrset(
    token: str,
    domain: str,
    subname: str,
    rtype: str,
    ttl: int,
    records: list[str],
) -> dict:
    """
    Replace the records in an existing RRset (patch operation).

    The entire record set is replaced — any records not in the `records` list
    will be removed.

    Parameters: same as create_rrset()

    Returns:
      dict with the updated RRset

    Raises:
      httpx.HTTPStatusError — 404 if the RRset doesn't exist (use create or upsert)
    """
    payload = {"ttl": ttl, "records": records}
    r = httpx.patch(
        f"{get_api_base()}/domains/{domain}/rrsets/{subname}/{rtype}/",
        headers=api_headers(token),
        json=payload,
        timeout=10,
    )
    r.raise_for_status()
    return r.json()


def delete_rrset(token: str, domain: str, subname: str, rtype: str) -> None:
    """
    Delete an RRset (and all its records) from a domain.

    Parameters:
      token   — API token with write access
      domain  — domain name
      subname — subdomain label (empty string = apex)
      rtype   — DNS record type to delete

    Raises:
      httpx.HTTPStatusError — on API error
    """
    r = httpx.delete(
        f"{get_api_base()}/domains/{domain}/rrsets/{subname}/{rtype}/",
        headers=api_headers(token),
        timeout=10,
    )
    r.raise_for_status()


def upsert_rrset(
    token: str,
    domain: str,
    subname: str,
    rtype: str,
    ttl: int,
    records: list[str],
) -> dict:
    """
    Create an RRset if it doesn't exist, or update it if it does.

    This is the "safe" version that works whether or not the record exists.
    Used by the provisioning wizards which need to set initial address records
    without knowing if they're already there.

    Internally: tries create first; if the API returns 400 or 409 (already
    exists), falls back to update.

    Parameters: same as create_rrset()

    Returns:
      dict with the created or updated RRset

    Raises:
      httpx.HTTPStatusError — if both create and update fail
    """
    try:
        return create_rrset(token, domain, subname, rtype, ttl, records)
    except httpx.HTTPStatusError as e:
        # 400 Bad Request and 409 Conflict both indicate the record already exists
        if e.response.status_code in (400, 409):
            return update_rrset(token, domain, subname, rtype, ttl, records)
        raise  # re-raise any other error (401, 403, 500, etc.)


# ──────────────────────────────────────────────────────────────────────────────
# Provisioning wizards
# ──────────────────────────────────────────────────────────────────────────────
#
# These functions combine multiple API calls into useful one-shot operations.
# They are called by the provisioning modals in tui/screens/provision.py and
# by the CLI subcommands (ddns-add, cert-add, cert-multi) in desec.py.


def _acme_subname(subname: str) -> str:
    """
    Compute the _acme-challenge subdomain label for ACME DNS-01 challenges.

    ACME DNS-01 cert validation requires that the certificate authority can
    query a TXT record at _acme-challenge.<hostname>.  For example:
      subname="www"  → "_acme-challenge.www"
      subname=""     → "_acme-challenge"   (apex / root)

    Parameters:
      subname — the subdomain label of the host being certified

    Returns:
      str — the full _acme-challenge label to use as the RRset subname
    """
    return f"_acme-challenge.{subname}" if subname else "_acme-challenge"


def provision_ddns_token(
    master_token: str,
    name: str,
    domain: str,
    subname: str,
    ipv4: str | None,
    ipv6: str | None,
    ttl: int = 3600,
) -> dict:
    """
    Provision a DDNS-scoped API token for a single hostname.

    DDNS (Dynamic DNS) allows a host to update its own DNS records as its IP
    changes (e.g. a home router with a dynamic public IP).

    This wizard:
      1. Optionally sets initial A record (IPv4) and/or AAAA record (IPv6)
         using the master token — so DNS is immediately pointing somewhere.
      2. Creates a NEW token with ONLY write access to A and AAAA records at
         that specific domain+subname.  The token cannot touch any other record,
         subdomain, or domain.

    The returned token is what you configure in your DDNS client (e.g. ddclient,
    inadyn, or a custom curl script).

    Parameters:
      master_token — your full-access deSEC token
      name         — label for the new scoped token
      domain       — domain name (e.g. "myhost.dedyn.io" or "example.com")
      subname      — subdomain label (empty string = use the domain apex)
      ipv4         — initial IPv4 address to set (None = don't set)
      ipv6         — initial IPv6 address to set (None = don't set)
      ttl          — time-to-live for any initial records (default: 1 hour)

    Returns:
      dict — the newly created token (contains one-time "token" secret field)
    """
    # Step 1: Set initial DNS records if addresses were provided.
    # This uses the master token because the new scoped token doesn't exist yet.
    if ipv4:
        upsert_rrset(master_token, domain, subname, "A", ttl, [ipv4])
    if ipv6:
        upsert_rrset(master_token, domain, subname, "AAAA", ttl, [ipv6])

    # Step 2: Create the scoped token.  It has no special domain/token permissions.
    tok = create_token(
        master_token, name,
        perm_manage_tokens=False,
        perm_create_domain=False,
        perm_delete_domain=False,
        allowed_subnets=[],
        max_unused_period=None,
        auto_policy=False,
    )
    tok_id = tok["id"]

    # Step 3: deSEC REQUIRES a default catch-all policy (deny everything) before
    # any scoped policies can be added.  This is a hard API constraint.
    create_policy(master_token, tok_id, None, None, None, perm_write=False)

    # Step 4: Grant write access to A and AAAA records for this specific hostname.
    # We grant both A and AAAA regardless of which initial values were provided —
    # the DDNS client needs to be able to update both address families over time.
    create_policy(master_token, tok_id, domain, subname or None, "A",    perm_write=True)
    create_policy(master_token, tok_id, domain, subname or None, "AAAA", perm_write=True)

    return tok


def provision_cert_token(
    master_token: str,
    name: str,
    domain: str,
    subname: str,
    ipv4: str | None,
    ipv6: str | None,
    cname: str | None,
    ttl: int = 3600,
) -> dict:
    """
    Provision a single-domain cert-scoped API token for ACME DNS-01 challenges.

    Use this to generate a TLS certificate using Let's Encrypt or another ACME CA
    with the DNS-01 challenge method.  DNS-01 proves domain ownership by creating
    a TXT record at _acme-challenge.<hostname>.

    This wizard:
      1. Optionally sets initial address (A/AAAA) or CNAME records using the
         master token.  These are NOT granted to the new token — the cert client
         doesn't need to change them.
      2. Creates a NEW token with ONLY write access to the TXT record at
         _acme-challenge.<hostname>.  The token cannot touch any other record.

    The returned token is what you configure in your ACME client
    (e.g. certbot --dns-desec, acme.sh --dns desec).

    Parameters:
      master_token — your full-access deSEC token
      name         — label for the new scoped token
      domain       — domain name
      subname      — subdomain label of the host to certify (empty = apex)
      ipv4         — initial A record to set (None = don't set)
      ipv6         — initial AAAA record to set (None = don't set)
      cname        — initial CNAME target (mutually exclusive with ipv4/ipv6)
      ttl          — TTL for any initial records

    Returns:
      dict — the newly created token (contains one-time "token" secret field)
    """
    # Step 1: Set initial address or CNAME records if provided
    if cname:
        # CNAME takes priority over address records (they'd conflict anyway)
        upsert_rrset(master_token, domain, subname, "CNAME", ttl, [cname])
    else:
        if ipv4:
            upsert_rrset(master_token, domain, subname, "A", ttl, [ipv4])
        if ipv6:
            upsert_rrset(master_token, domain, subname, "AAAA", ttl, [ipv6])

    # Step 2: Create the scoped token
    tok = create_token(
        master_token, name,
        perm_manage_tokens=False,
        perm_create_domain=False,
        perm_delete_domain=False,
        allowed_subnets=[],
        max_unused_period=None,
        auto_policy=False,
    )
    tok_id = tok["id"]

    # Step 3: Required catch-all deny policy (must exist before scoped policies)
    create_policy(master_token, tok_id, None, None, None, perm_write=False)

    # Step 4: Grant write access ONLY to the _acme-challenge TXT record.
    # _acme_subname("www") → "_acme-challenge.www"
    # _acme_subname("")    → "_acme-challenge"
    create_policy(master_token, tok_id, domain, _acme_subname(subname), "TXT", perm_write=True)

    return tok


def provision_cert_multi_token(
    master_token: str,
    name: str,
    entries: list[tuple[str, str]],
) -> dict:
    """
    Provision a multi-domain cert-scoped token (TXT-only, no address access).

    Use this when a single TLS certificate covers multiple hostnames (a SAN cert).
    The token gets write access to _acme-challenge TXT records for every listed
    hostname, and nothing else — strict least-privilege.

    Unlike provision_cert_token(), this wizard does NOT set any initial address
    records.  The assumption is the hostnames are already configured.

    Parameters:
      master_token — your full-access deSEC token
      name         — label for the new scoped token
      entries      — list of (domain, subname) tuples, one per hostname
                     e.g. [("example.dedyn.io", "www"), ("example.dedyn.io", "")]

    Returns:
      dict — the newly created token (contains one-time "token" secret field)
    """
    # Create the token first
    tok = create_token(
        master_token, name,
        perm_manage_tokens=False,
        perm_create_domain=False,
        perm_delete_domain=False,
        allowed_subnets=[],
        max_unused_period=None,
        auto_policy=False,
    )
    tok_id = tok["id"]

    # Required catch-all deny policy
    create_policy(master_token, tok_id, None, None, None, perm_write=False)

    # Grant TXT write at _acme-challenge for each requested hostname
    for domain, subname in entries:
        create_policy(
            master_token, tok_id,
            domain, _acme_subname(subname), "TXT",
            perm_write=True,
        )

    return tok
