import os, tempfile, json
import requests
from urllib.parse import parse_qs, urlparse
import catalog_discover_festivals as festivals

class Response:
    def __init__(self, payload, status=200): self.payload, self.status_code = payload, status
    def raise_for_status(self):
        if self.status_code >= 400: raise requests.HTTPError(str(self.status_code))
    def json(self): return self.payload
class Session:
    def __init__(self, responses): self.responses, self.calls = list(responses), []
    def get(self, url, **kwargs): self.calls.append((url, kwargs)); return self.responses.pop(0)
def event(i="abc", cancelled=False):
    return {"id": i, "name": "World Music Fest", "cancelled": cancelled, "life-span": {"begin": "2027-07-01", "end": "2027-07-03"}, "area": {"name": "Canada", "iso-3166-1-codes": ["CA"]}, "relations": [{"type": "official homepage", "url": {"resource": "https://fest.example"}}, {"type": "schedule", "url": {"resource": "https://fest.example/schedule"}}, {"type": "held at", "place": {"coordinates": {"latitude": "45.5", "longitude": "-73.6"}}}]}
def run():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "state.json")
        session = Session([Response({"count": 1, "events": [{"id": "abc"}]}), Response(event())])
        result = festivals.discover(session, limit=20, state_path=path, today="2027-01-01", sleep=None)
        c = result["candidates"][0]; assert c["status"] == "pending" and c["country"] == "CA"; assert c["coordinates"] == {"latitude": 45.5, "longitude": -73.6}; assert result["cursor"]["offset"] == 0
        query = parse_qs(urlparse(session.calls[0][0]).query)["query"][0]
        assert "type:festival" in query and "begin:[2027-01-01 TO 2028-01-01]" in query
        assert "inc=url-rels%2Bplace-rels%2Barea-rels" in session.calls[1][0]
    refused = Session([Response({}, 429)])
    try: festivals.discover(refused, state_path=None)
    except RuntimeError as exc: assert "no retry" in str(exc)
    else: raise AssertionError("429 should fail")
    assert festivals.candidate_from_event(event("x", True)) is None
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, 'state.json')
        # A detail failure checkpoints only the fully processed prefix.
        s = Session([Response({'count':3,'events':[{'id':'a'},{'id':'b'}]}),Response(event('a')),Response({},429)])
        r = festivals.discover(s,state_path=path,today='2027-01-01',sleep=None)
        assert r['cursor']['offset'] == 1 and r['stats']['failure']
        assert len(r['candidates']) == 1
        r['candidates'][0]['status'] = 'rejected'
        with open(path,'w') as f: json.dump(r,f)
        # Next day's resumed query keeps its original date window.
        s = Session([Response({'count':3,'events':[{'id':'a'}]}),Response(event('a'))])
        r = festivals.discover(s,state_path=path,today='2027-01-02',sleep=None)
        query = parse_qs(urlparse(s.calls[0][0]).query)['query'][0]
        assert 'begin:[2027-01-01 TO 2028-01-01]' in query
        assert r['candidates'][0]['status'] == 'rejected'
        before = open(path).read()
        try:
            festivals.discover(Session([Response({'count':3,'events':None})]),state_path=path)
        except ValueError: pass
        else: raise AssertionError('malformed response accepted')
        assert open(path).read() == before
    print("ok discover festivals")
if __name__ == "__main__": run()
