"""Render the actual README SVG and reject clipped/overlapping text or crossed cards.

python scripts/check_architecture.py --output-dir <preview-folder>
"""
import argparse
import json
import os
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    os.environ['PLAYWRIGHT_SCREENSHOT_NO_FONTS_READY'] = '1'
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=['--disable-gpu'])
        page = browser.new_page(viewport={'width':1600,'height':1300}, device_scale_factor=1)
        svg = (ROOT / 'docs/architecture.svg').read_text(encoding='utf-8')
        page.set_content('<html><body style="margin:0">' + svg + '</body></html>')
        checks = page.evaluate('''() => {
          const svg=document.querySelector('svg'), issues=[];
          const cards=[...svg.querySelectorAll('g[data-card]')];
          for(const g of cards) {
            const x=+g.dataset.x,y=+g.dataset.y,w=+g.dataset.w,h=+g.dataset.h;
            for(const t of g.querySelectorAll('text')) {
              const b=t.getBBox();
              if(b.x<x+10||b.x+b.width>x+w-10||b.y<y+10||b.y+b.height>y+h-10)
                issues.push({kind:'card-overflow',card:g.dataset.card,text:t.textContent});
            }
          }
          const texts=[...svg.querySelectorAll('text')].map(t=>({text:t.textContent,b:t.getBBox()}));
          for(let i=0;i<texts.length;i++) {
            const a=texts[i];
            if(a.b.x<0||a.b.y<0||a.b.x+a.b.width>1600||a.b.y+a.b.height>1300)
              issues.push({kind:'canvas-overflow',text:a.text});
            for(let j=i+1;j<texts.length;j++) {
              const b=texts[j];
              if(Math.min(a.b.x+a.b.width,b.b.x+b.b.width)-Math.max(a.b.x,b.b.x)>2 &&
                 Math.min(a.b.y+a.b.height,b.b.y+b.b.height)-Math.max(a.b.y,b.b.y)>2)
                issues.push({kind:'text-overlap',texts:[a.text,b.text]});
            }
          }
          for(const path of svg.querySelectorAll('path[marker-end]')) {
            const points=path.getAttribute('d').match(/[-\\d.]+,[-\\d.]+/g).map(s=>s.split(',').map(Number));
            for(let i=1;i<points.length;i++) {
              const [a,b]=[points[i-1],points[i]];
              for(const g of cards) {
                const x=+g.dataset.x+2,y=+g.dataset.y+2,w=+g.dataset.w-4,h=+g.dataset.h-4;
                const cross=a[0]===b[0] ? a[0]>x&&a[0]<x+w&&Math.min(a[1],b[1])<y+h&&Math.max(a[1],b[1])>y :
                  a[1]===b[1] && a[1]>y&&a[1]<y+h&&Math.min(a[0],b[0])<x+w&&Math.max(a[0],b[0])>x;
                if(cross)issues.push({kind:'edge-crosses-card',card:g.dataset.card});
              }
            }
          }
          return {cards:cards.length,texts:texts.length,issues};
        }''')
        page.screenshot(path=str(args.output_dir/'architecture-full.png'), timeout=15000)
        page.set_viewport_size({'width':960,'height':800})
        page.add_style_tag(content='body>svg{width:960px;height:auto;display:block}')
        page.screenshot(path=str(args.output_dir/'architecture-readme.png'), full_page=True, timeout=15000)
        browser.close()
    (args.output_dir/'layout-check.json').write_text(json.dumps(checks, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(checks, ensure_ascii=True))
    if checks['issues']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
