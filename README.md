# desec

deSEC DNS manager — manage tokens, policies, domains, and DNS records on [desec.io](https://desec.io).

Three interfaces in one tool:

| Interface | How to launch | Requires |
|---|---|---|
| TUI (Textual terminal UI) | `python desec.py` | `pip install textual httpx` |
| CLI (scriptable) | `python desec.py <subcommand>` | `pip install httpx` |
| GUI (Qt native) | `python desec.py --gui` | `pip install PySide6` |

## Quick start

```bash
git clone https://github.com/bi0shacker001/desec.git
cd desec
pip install textual httpx pyyaml   # core deps; add PySide6 for GUI
python desec.py                    # launches TUI; prompts for API token on first run
```

Your token is saved to `~/.config/mech-goodies/desec.env` and reused automatically.

## CLI usage

```bash
# Token management
python desec.py token list
python desec.py token create --name my-token
python desec.py token delete TOKEN_ID

# Domain management
python desec.py domain list
python desec.py domain create example.dedyn.io

# DNS records
python desec.py record list example.dedyn.io
python desec.py record create example.dedyn.io --type A --subname @ --ttl 300 --records 1.2.3.4
python desec.py record delete example.dedyn.io --type A --subname @

# Token provisioning wizards
python desec.py ddns-add example.dedyn.io         # DDNS token (dynamic IP updates)
python desec.py cert-add example.dedyn.io         # ACME/cert token (single domain)
python desec.py cert-multi --entry example.dedyn.io --entry sub.other.dedyn.io  # multi-domain cert token
```

## TUI features

- Login screen — auto-connects if token is saved; verifies against the API
- Token list — create, delete, provision DDNS/cert tokens with guided wizards
- Policy screen — add/delete per-token IP/subnet policies; auto-fixes the deSEC "policy precedence" 400 error
- Domain list — create and delete domains
- RRSet screen — browse, add, edit, delete DNS records with CNAME quick-pick

## Configuration

`~/.config/mech-goodies/desec.env`:

```env
DESEC_TOKEN=your_token_here
DESEC_API_BASE=https://desec.io/api/v1   # optional; defaults to this value
```

## Project layout

```
desec.py          Entry point — TUI / CLI / GUI dispatcher
core/
  env.py          Config file read/write (~/.config/mech-goodies/desec.env)
  api.py          All deSEC HTTP API calls (httpx, synchronous)
  output.py       Table/JSON/YAML output helpers; --token resolution
tui/
  app.py          DeSECApp (Textual App subclass) + global CSS
  widgets.py      Shared modals: MessageModal, ConfirmModal, NewSecretModal
  screens/
    login.py      LoginScreen — token entry and verification
    tokens.py     TokenListScreen + CreateTokenModal
    policies.py   PolicyScreen + AddPolicyModal (with auto-fix logic)
    domains.py    DomainListScreen + CreateDomainModal
    rrsets.py     RRSetScreen + AddEditRRSetModal
    provision.py  DDNS / cert / cert-multi provisioning wizards
gui/
  app.py          PySide6 stub (placeholder)
```

## Troubleshooting

| Symptom | Fix |
|---|---|
| `No API token found` | Set `DESEC_TOKEN` in `~/.config/mech-goodies/desec.env` or pass `--token YOUR_TOKEN` |
| TUI won't start | `pip install textual` |
| GUI won't start | `pip install PySide6` |
| API 401 | Token is wrong or expired — generate a new one at desec.io |
| API 403 | Token lacks `perm_manage_tokens` — needed for most token operations |
| Policy 400 error | TUI PolicyScreen offers to auto-fix by creating a default catch-all policy |
