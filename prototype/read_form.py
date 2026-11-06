# Reads a form's structure and prints it as JSON. No personal data, no typing, no submit.
import asyncio,json,sys
from playwright.async_api import async_playwright
URL="https://jobs.ashbyhq.com/devsavant/51c02958-afac-4589-8be5-0339dc1003dd/application"
JS=r"""()=>{const out=[];const seen=new Set();
const txt=e=>(e?.innerText||'').replace(/\s+/g,' ').trim().slice(0,400);
for(const el of document.querySelectorAll('input,textarea,select,button[role=combobox],[role=combobox],[role=radio],[role=checkbox]')){
 if(el.type==='hidden'||el.closest('[aria-hidden=true]'))continue;
 let box=el.closest('fieldset')||el.closest('[class*=fieldEntry]')||el.closest('[class*=question]')||el.parentElement;
 const key=box; if(seen.has(key))continue; seen.add(key);
 const opts=[...box.querySelectorAll('label')].map(txt).filter(Boolean).slice(0,10);
 out.push({tag:el.tagName,type:el.type||'',name:el.name||el.id||'',required:el.required||el.getAttribute('aria-required')==='true',text:txt(box),options:opts});}
return out}"""
async def main():
    async with async_playwright() as p:
        b=await p.chromium.launch(headless=True,args=["--no-sandbox"]);pg=await b.new_page()
        await pg.goto(URL,wait_until="networkidle",timeout=45000);await pg.wait_for_timeout(2000)
        print(json.dumps(await pg.evaluate(JS),ensure_ascii=False));await b.close()
asyncio.run(main())
