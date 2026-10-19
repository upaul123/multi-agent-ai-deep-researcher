"""Offline credential-pattern scan; reports locations only, with nonzero exit on candidates."""
from pathlib import Path
import re, subprocess
root=Path(__file__).resolve().parents[1]
def git(*args):
    return subprocess.check_output(['git', '-C', str(root), *args], stderr=subprocess.DEVNULL)
patterns={
'OpenAI/OpenRouter key':re.compile(r'\bsk-(?:or-v1-|proj-|svcacct-)?[A-Za-z0-9_-]{20,}\b'),
'Tavily key':re.compile(r'\btvly-[A-Za-z0-9_-]{20,}\b'),
'GitHub token':re.compile(r'\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})\b'),
'AWS access key':re.compile(r'\b(?:AKIA|ASIA)[A-Z0-9]{16}\b'),
'Google API key':re.compile(r'\bAIza[A-Za-z0-9_-]{30,}\b'),
'Google OAuth secret':re.compile(r'\bGOCSPX-[A-Za-z0-9_-]{20,}\b'),
'JWT credential':re.compile(r'\beyJ[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{15,}\b'),
'Private key':re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----'),
'Credential-bearing URL':re.compile(r'\b(?:https?|postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis)://[^\s/:]+:[^\s/@]{8,}@'),
'Credential assignment':re.compile(r'''(?im)^\s*["']?[\w.-]*(?:api_key|auth_token|client_secret|cookie_secret|password|access_token)["']?\s*[:=]\s*["']([A-Za-z0-9_./+=-]{20,})["']''')}
seen=set(); findings=[]
def scan(label,path,data):
    try: text=data.decode('utf-8')
    except UnicodeDecodeError: return
    for kind,pattern in patterns.items():
        for match in pattern.finditer(text):
            value=match.group(1) if kind=='Credential assignment' else match.group(0)
            if any(x in value.lower() for x in ('example','placeholder','your_','test-','test_','fake-','dummy')): continue
            findings.append((label,path,text.count('\n',0,match.start())+1,kind))
tracked=git('ls-files','--cached','--others','--exclude-standard','-z').decode().split('\0')
for path in filter(None,tracked):
    p=root/path
    if p.is_file(): scan('working tree',path,p.read_bytes())
commits=git('rev-list','--all').decode().splitlines()
for rev in commits:
    for entry in git('ls-tree','-r','-z',rev).split(b'\0'):
        if not entry: continue
        meta,path=entry.split(b'\t',1)
        mode,kind,oid=meta.split()
        if kind != b'blob' or oid in seen: continue
        seen.add(oid)
        scan(rev[:12],path.decode(errors='replace'),git('cat-file','blob',oid.decode()))
print(f'Scanned {len(list(filter(None,tracked)))} tracked/non-ignored working-tree paths, {len(commits)} reachable commits, {len(seen)} unique historical blobs.')
for label,path,line,kind in findings:
    print(f'CANDIDATE: {label} {path}:{line} [{kind}] — value withheld')
print(f'Credential candidates: {len(findings)}. Pattern scan only; credentials were not sent to external services.')

raise SystemExit(1 if findings else 0)
