import json, urllib.request

class GitHubClient:
    def __init__(self, token: str, repo: str):
        self.token = token
        self.repo = repo

    def open_pr(self, title: str, head: str, base: str, body: str):
        url = f"https://api.github.com/repos/{self.repo}/pulls"
        data = json.dumps({"title": title, "head": head, "base": base, "body": body}).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("Authorization", f"token {self.token}")
        req.add_header("Accept", "application/vnd.github+json")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            return {"error": str(e)}
