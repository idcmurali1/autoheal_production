
import re
class SimpleRetriever:
    def __init__(self, html_text: str):
        self.html = html_text
    def candidate_ids(self):
        return re.findall(r'id="([^"]+)"', self.html)
    def guess_replacement_for(self, old_id: str):
        ids = self.candidate_ids()
        btn_ids = [i for i in ids if i.lower().startswith('btn')]
        if btn_ids:
            prefs = ['proceed','continue','submit','checkout']
            for p in prefs:
                for cand in btn_ids:
                    if p in cand.lower():
                        return cand
            return btn_ids[0]
        return ids[0] if ids else None
