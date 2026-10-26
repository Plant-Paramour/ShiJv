"""Do not forward authenticated requests through redirects."""
from urllib.request import HTTPRedirectHandler, ProxyHandler, build_opener


class RejectRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


model_urlopen = build_opener(RejectRedirects()).open
local_urlopen = build_opener(ProxyHandler({}), RejectRedirects()).open
