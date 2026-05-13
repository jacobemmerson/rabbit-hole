Verify that the production deployment on the Dell laptop server is healthy. Checks Nginx, Uvicorn (via systemd), and the Let's Encrypt certificate.

**Usage:** `/deploy-check`

## Steps

1. Check the systemd service status for Uvicorn:
   ```bash
   systemctl status rabbit-hole   # or whatever the service is named
   ```
   Confirm it is `active (running)` and note the uptime and last restart time.

2. Check Nginx status and configuration:
   ```bash
   systemctl status nginx
   nginx -t
   ```
   Confirm the config test passes and Nginx is running.

3. Check the Let's Encrypt certificate expiry:
   ```bash
   certbot certificates
   ```
   Or use openssl if the domain is reachable:
   ```bash
   echo | openssl s_client -connect <domain>:443 2>/dev/null | openssl x509 -noout -dates
   ```
   Flag if the cert expires within 14 days.

4. Check that the app responds:
   ```bash
   curl -s -o /dev/null -w "%{http_code}" https://<domain>/
   ```
   Expect a 200. If 502, Uvicorn is likely down. If 301/302, check Nginx redirect config.

5. Check recent Uvicorn logs for errors:
   ```bash
   journalctl -u rabbit-hole --since "1 hour ago" --no-pager
   ```

6. Check Nginx error log for upstream errors:
   ```bash
   tail -n 50 /var/log/nginx/error.log
   ```

7. Output a health summary:
   - Uvicorn: UP / DOWN (uptime, last restart)
   - Nginx: UP / DOWN (config valid: yes/no)
   - TLS cert: valid until <date> (days remaining)
   - HTTP check: <status code>
   - Recent errors: none / list of issues found in logs

8. If any check fails, suggest the likely fix.
