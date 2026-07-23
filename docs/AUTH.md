# Authentication & Credential Management

Agent47 uses a **local-first** authentication architecture.  Your identity and API keys are stored
entirely on your machine — in the OS credential manager and a local config directory — with no cloud
accounts, no telemetry, and no external service dependencies.

## Overview

| Capability | Implementation |
|---|---|
| Identity | Google OAuth 2.0 Authorization Code + PKCE |
| API Keys | BYOK (Bring Your Own Key) stored in OS keyring |
| Token Storage | Windows Credential Manager, macOS Keychain, Linux Secret Service |
| Profile Storage | Local JSON file in the config directory (no secrets) |
| Scopes | `openid`, `email`, `profile` (identity only) |

## Google OAuth Setup

### 1. Create a Google Cloud Project

1. Go to [console.cloud.google.com](https://console.cloud.google.com)
2. Create a new project (or select an existing one)
3. Enable the **Google Identity** API (no other APIs are needed)

### 2. Create a Desktop OAuth Client

1. Navigate to **APIs & Services → Credentials**
2. Click **Create Credentials → OAuth client ID**
3. Application type: **Desktop app**
4. Name: `Agent47` (or any name you prefer)
5. Click **Create**
6. Copy the **Client ID** (and optionally the **Client Secret**)

### 3. Configure Agent47

Add these to your `.env` file or set them as environment variables:

```bash
GOOGLE_CLIENT_ID=your-client-id.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=your-client-secret   # Optional for Desktop apps
GOOGLE_OAUTH_TIMEOUT_SECONDS=180          # Optional, default 180
```

> **Note**: Desktop OAuth clients in Google Cloud do *not* require a client secret for the
> Authorization Code + PKCE flow.  The PKCE verifier replaces the secret for client authentication.

### 4. Redirect URI

Agent47 uses a **dynamically allocated localhost port**:

```
http://127.0.0.1:<auto-port>/callback
```

In the Google Cloud Console under your OAuth client's **Authorized redirect URIs**, you do **not**
need to add any redirect URIs.  Google's OAuth for Desktop apps allows any `http://127.0.0.1:*`
redirect by default.

## Signing In

```bash
agent47 auth login
```

This will:
1. Generate a cryptographically secure PKCE code verifier and random state
2. Start a single-use HTTPS-free listener on `127.0.0.1` with an auto-allocated port
3. Open your default browser to Google's authorization page
4. Wait for Google to redirect back to the local listener
5. Validate the state parameter (timing-safe comparison to prevent CSRF)
6. Exchange the authorization code for tokens using the PKCE verifier
7. Fetch your Google profile (name, email)
8. Store tokens in the OS credential manager (never on disk)
9. Store your profile (non-sensitive metadata only) in a local JSON file

If the browser cannot be opened automatically, the authorization URL is printed to the terminal so
you can open it manually.

## Session Management

```bash
# Check authentication status
agent47 auth status

# Force-refresh the access token
agent47 auth refresh

# Repair broken authentication state
agent47 auth repair

# Sign out and remove all credentials
agent47 auth logout
agent47 auth logout --remove-keys  # Also remove stored API keys
```

### `auth status`

Shows detailed session information:

```
─── Authentication ───
  ✓ Logged In
  ✓ Access Token
  ✓ Refresh Token

  Provider:   Google
  Email:      you@gmail.com
  Name:       Your Name
  Created:    2026-07-23T12:00:00+00:00
  Last Login: 2026-07-23T12:00:00+00:00
```

### `auth repair`

Automatically recovers from common authentication problems:

- **Stale profile, no tokens**: Removes the orphaned profile
- **Tokens, no profile**: Fetches the profile from Google using stored tokens
- **Expired access token**: Refreshes using the stored refresh token
- **Revoked refresh token**: Clears the session and prompts for re-authentication

## BYOK (Bring Your Own Key)

Agent47 supports multiple LLM providers.  API keys are stored in the OS credential manager and
**never** appear in `.env`, logs, error messages, or debug output.

### Managing Keys

```bash
# Add a new key (validated before storage)
agent47 keys add openai
agent47 keys add gemini --skip-validation  # Skip provider check

# List configured providers
agent47 keys list

# Test a stored key
agent47 keys test openai

# Rotate (replace) an existing key
agent47 keys rotate openai

# Remove a stored key
agent47 keys remove openai
```

### Supported Providers

| Provider | Environment Variable | Keyring Name |
|---|---|---|
| OpenAI | `OPENAI_API_KEY` | `provider:openai` |
| Anthropic | `ANTHROPIC_API_KEY` | `provider:anthropic` |
| Google Gemini | `GEMINI_API_KEY` | `provider:gemini` |
| OpenRouter | `OPENROUTER_API_KEY` | `provider:openrouter` |
| Groq | `GROQ_API_KEY` | `provider:groq` |
| DeepSeek | `DEEPSEEK_API_KEY` | `provider:deepseek` |
| NVIDIA NIM | `NVIDIA_API_KEY` | `provider:nvidia` |

### Key Priority

When resolving an API key, Agent47 checks in this order:

1. **OS keyring** (via `agent47 keys add`)
2. **Environment variable** / `.env` file

Keyring-stored keys always take precedence over environment variables.

### Migrating from .env

If you have API keys in your `.env` file, you can migrate them to secure storage:

```bash
agent47 keys migrate                 # Interactive migration
agent47 keys migrate --remove        # Also remove from .env after migration
agent47 keys migrate --env-file path/to/.env
```

The migration process:
1. Scans the `.env` file for known provider key patterns
2. For each key found, asks whether to import it
3. Stores accepted keys in the OS credential manager
4. Verifies successful storage by reading back
5. Optionally removes migrated keys from the `.env` file
6. **Never displays any key values**

## Secure Credential Storage

### Windows

Credentials are stored in **Windows Credential Manager** using DPAPI encryption.

- View: Control Panel → Credential Manager → Windows Credentials
- Entries appear under `Agent47` in the Generic Credentials section
- Encrypted at rest using your Windows login credentials

### macOS

Credentials are stored in the **macOS Keychain**.

- View: Keychain Access → login → search for "Agent47"
- Protected by the system keychain with your login password

### Linux

Credentials are stored via the **Secret Service D-Bus API** using `libsecret`.

**Required packages** (one of):

```bash
# GNOME
sudo apt install gnome-keyring libsecret-1-0
pip install keyring SecretStorage

# KDE
sudo apt install kwalletmanager
pip install keyring

# If D-Bus session is not available (e.g., SSH, containers):
dbus-run-session -- agent47 auth login
```

### Insecure Backend Detection

Agent47 **refuses** to store credentials if the keyring backend falls back to `PlaintextKeyring`
or `NullKeyring`.  You'll see an error with platform-specific setup instructions.

## Diagnostics

```bash
agent47 doctor
```

The doctor command checks:

| Check | Description |
|---|---|
| `authentication` | Is a Google account profile stored? |
| `session` | Are OAuth tokens available with a refresh token? |
| `keyring` | Is the OS credential store accessible and secure? |
| `stored-api-keys` | How many provider keys are in the keyring? |
| `internet` | Can DNS resolve test hosts? |
| `provider-reachability` | Can TCP connect to the configured provider? |
| `container-runtime` | Is Docker or Podman available? |
| `python-version` | Is Python ≥ 3.11? |
| `platform` | Is the OS supported? |
| `workspace-writable` | Can Agent47 write to the workspace? |
| `sqlite-storage` | Is the SQLite database accessible? |
| `api-key` | Is a provider API key configured? |

## Troubleshooting

### Browser doesn't open during login

When the browser cannot be opened automatically, Agent47 prints the authorization URL to the
terminal.  Copy it and open it manually in any browser.

### "Secure credential storage is unavailable"

Install the `keyring` package and a platform-appropriate backend:

```bash
pip install keyring
# Linux also needs:
pip install SecretStorage
sudo apt install gnome-keyring libsecret-1-0
```

### "The active keyring backend is not secure"

The `keyring` library has fallen back to a plaintext or null backend.  This usually happens on
Linux when `gnome-keyring` or `KWallet` is not running.

Fix: Start the keyring daemon or use `dbus-run-session`:

```bash
dbus-run-session -- agent47 auth login
```

### Login times out

The default timeout is 180 seconds.  Increase it:

```bash
GOOGLE_OAUTH_TIMEOUT_SECONDS=300 agent47 auth login
```

### "Security state did not match"

This indicates a potential CSRF attack or a stale browser tab.  Close all Agent47 login tabs and
try again.  Each login generates a fresh state parameter.

### Token refresh fails

If the refresh token has been revoked (e.g., you changed your Google password or revoked access
in your Google Account settings), run:

```bash
agent47 auth login
```

### "Could not start the local sign-in listener"

Another process is using the required port.  Agent47 uses auto-allocated ports, so this is rare.
Close conflicting applications and try again.

## Security Design

### OAuth 2.1 Compliance

- **PKCE**: S256 only, no fallback to `plain`
- **Code verifier**: 86 characters from `secrets.token_urlsafe(64)` (OS CSPRNG)
- **State parameter**: 43 characters from `secrets.token_urlsafe(32)`
- **State validation**: Timing-safe comparison via `secrets.compare_digest()`
- **Callback server**: Binds to `127.0.0.1` only, auto-allocated port, single-use
- **Replay protection**: Second callback returns 409 Conflict
- **HTTP method restriction**: Only GET accepted, all others return 405
- **Timeout**: Configurable with 180-second default, max 900 seconds
- **Log suppression**: Callback handler silences all request logging
- **Security headers**: `Cache-Control: no-store`, `X-Content-Type-Options: nosniff`

### Secret Handling

- OAuth tokens are **never** written to disk — only stored in the OS keyring
- API keys are **never** logged, printed, or included in error messages
- Token allowlist filtering strips unknown fields before keyring storage
- Account profile files contain only non-sensitive identity metadata
- Error messages are safe for display — they never contain tokens or codes
