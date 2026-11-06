# Dry run of the generic filler with dummy data: never submits.
import sys,json
sys.path.insert(0,'/w/src')
from relevo import ashby, filler
open('/w/test.pdf','wb').write(b'%PDF-1.4\n%%EOF\n')
JOBS={'devsavant':'https://jobs.ashbyhq.com/devsavant/51c02958-afac-4589-8be5-0339dc1003dd','nabla':'https://jobs.ashbyhq.com/nabla/c2d355f7-0589-42f5-8a38-7f1785ae9187'}
for name,url in JOBS.items():
    form=ashby.fetch_form(url);a={}
    for f in form.fields:
        if f.kind=='file': a[f.id]='/w/test.pdf'
        elif f.kind=='email': a[f.id]='test@example.com'
        elif f.kind=='number': a[f.id]='1'
        elif f.kind=='yes_no': a[f.id]='No'
        elif f.kind=='location': a[f.id]='Paris'
        elif f.kind=='multi_choice': a[f.id]=[f.options[0]]
        elif f.options: a[f.id]=f.options[-1] if 'country' in f.label else f.options[0]
        else: a[f.id]='Test Only'
    r=filler.fill(form,a,f'/w/{name}.png',submit=False)
    print(name,len(form.fields),'fields','ok',sum(r.verified.values()),'failed',r.failed,'submitted',r.submitted,'unknown',r.unknown)
