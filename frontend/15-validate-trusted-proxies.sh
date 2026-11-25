#!/bin/sh
set -eu

case "${REQUIRE_TRUSTED_PROXY:-false}" in
    true|false) ;;
    *) echo "REQUIRE_TRUSTED_PROXY must be true or false." >&2; exit 1 ;;
esac

# Only literal individual IPv4/IPv6 peers (/32 or /128 also permitted).
# nginx validates IPv6 syntax itself. Never resolve a hostname as a trust rule.
if ! awk -v required="${REQUIRE_TRUSTED_PROXY:-false}" '
{
    sub(/#.*/, ""); gsub(/^[ \t]+|[ \t]+$/, "");
    if ($0 == "") next;
    if (NF != 2 || $1 != "set_real_ip_from" || $2 !~ /;$/) { bad=1; exit }
    address=$2; sub(/;$/, "", address);
    count=split(address, parts, "/");
    if (count > 2) { bad=1; exit }
    if (index(parts[1], ":")) {
        if (parts[1] !~ /^[0-9a-fA-F:.]+$/ || (count == 2 && parts[2] != "128")) { bad=1; exit }
    } else {
        if (count == 2 && parts[2] != "32") { bad=1; exit }
        if (split(parts[1], octets, ".") != 4) { bad=1; exit }
        for (i=1; i<=4; i++) if (octets[i] !~ /^[0-9]+$/ || octets[i]+0 > 255) { bad=1; exit }
    }
    found=1;
}
END { if (bad || (required == "true" && !found)) exit 1 }
' /etc/nginx/trusted-proxies.conf; then
    echo "Invalid or missing exact trusted proxy configuration; refusing startup." >&2
    exit 1
fi
