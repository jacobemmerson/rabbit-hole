Decode and display a signed itsdangerous cookie from the Rabbit Hole app. Shows the parsed profile structure in readable form and flags any schema issues.

**Usage:** `/cookie-debug <raw-cookie-value>`

Pass the raw cookie string (with or without the `session=` prefix). If no argument is given, look for a running dev server and attempt to extract the cookie from a test request.

**Arguments:** $ARGUMENTS

## Steps

1. Strip the `session=` prefix if present.

2. Locate the `SECRET_KEY` used to sign cookies:
   - Check for a `.env` file, environment variable, or the app config
   - If not found, note that the payload can still be base64-decoded but signature verification will be skipped

3. Use Python's `itsdangerous` to decode the cookie:
   ```python
   from itsdangerous import URLSafeSerializer
   s = URLSafeSerializer(secret_key)
   data = s.loads(cookie_value)
   ```
   Or if the key is unavailable, decode the base64 payload directly to read the unsigned data.

4. Pretty-print the parsed cookie JSON with field-by-field annotations:
   - `timezone` — stored IANA timezone string
   - `last_generated` — date recommendations were last generated (compare to today in that timezone)
   - `selected_categories` — list of user-selected onboarding categories
   - `current_articles` — today's 5 recommended article titles
   - `seen_articles` — all articles ever shown (deduplication list)
   - `reading_history` — array of `{title, rating, scroll_depth}` objects

5. Flag any issues:
   - Missing required fields
   - `last_generated` date that would trigger an unexpected reset today
   - Articles in `current_articles` that also appear in `seen_articles`
   - Ratings outside the 1–5 range or scroll depths outside 0–100
   - Cookie size (itsdangerous has practical size limits around 4KB)
