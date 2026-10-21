import os, sys, re, json, hashlib, csv, threading, queue, tempfile, subprocess, ctypes, unicodedata, traceback
from pathlib import Path
from datetime import date, datetime
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import pypdfium2 as pdfium

VERSION = '1.1-mac'
BASE = Path(getattr(sys, '_MEIPASS', Path(__file__).parent))
COLORS = {'bg':'#f3f6fa','ink':'#16263d','blue':'#126b91','muted':'#607086'}

def norm(s):
    s = unicodedata.normalize('NFKC', s)
    s = ''.join(str(unicodedata.digit(c)) if c.isdigit() else c for c in s)
    return re.sub(r'[\u064b-\u065f\u0670\u0640\u200e\u200f\u202a-\u202e\u2066-\u2069]', '', s)

def compact(s): return re.sub(r'\s+', '', norm(s)).lower()

DATE = r'(?:20\d{2}[-/.]\d{1,2}[-/.]\d{1,2}|\d{1,2}[-/.]\d{1,2}[-/.]20\d{2})'

def parse_date(s):
    for fmt in ('%Y-%m-%d','%d-%m-%Y'):
        try: return datetime.strptime(re.sub(r'[/.]', '-', s), fmt).date().isoformat()
        except ValueError: pass
    return ''

def extract(text, filename=''):
    t = norm(text); c = compact(t)
    permits = sorted(set(re.findall(r'tq\d{5,12}',c)))
    ids = sorted(set(re.findall(r'(?<!\d)[12]\d{9}(?!\d)',c)))
    expiries = set()
    for label in ('تاريخنهايةالتصريح','permitexpirydate','expirydate','expirationdate','validuntil','تاريخانتهاءالتصريح'):
        for match in re.finditer(re.escape(label),c):
            after = re.match(r'[:：]*('+DATE+')',c[match.end():])
            before = re.search('('+DATE+r')[:：]*$',c[max(0,match.start()-15):match.start()])
            found = after or before
            if found:
                value = parse_date(found.group(1))
                if value: expiries.add(value)
    name = ''
    # In PDF extraction, English words can appear in visual RTL order. Use the
    # document name field as extracted and keep the filename visible for checking.
    m = re.search(r'اسم\s*العامل\s*([A-Za-z][A-Za-z .\-]{2,100})', t)
    if m: name = m.group(1).strip()
    if not name:
        m = re.search(r'([A-Z][A-Z .\-]{3,100})\s*اسم\s*العامل',t)
        if m: name=m.group(1).strip()
    file_name=re.sub(r'^\d+\s*[-–]\s*','',Path(filename).stem)
    file_name=re.sub(r'\b(ajeer|new|docs?|dox)\b','',file_name,flags=re.I).strip()
    file_words=re.findall(r'[A-Z]+',file_name.upper())
    if name and sorted(file_words)==sorted(re.findall(r'[A-Z]+',name.upper())):
        name=' '.join(file_words)
    reason=[]
    if len(permits)!=1: reason.append('Missing permit number' if not permits else 'Multiple permit numbers')
    if len(ids)!=1: reason.append('Missing Iqama number' if not ids else 'Multiple Iqama numbers')
    if len(expiries)!=1: reason.append('Expiry date missing, invalid or conflicting')
    return dict(name=name or Path(filename).stem, iqama=ids[0] if len(ids)==1 else '',
                permit=permits[0].upper() if len(permits)==1 else '',
                expiry=next(iter(expiries)) if len(expiries)==1 else '',
                issue='; '.join(reason), method='PDF text')

def bucket(r, today=None):
    if r.get('duplicate'): return 'Duplicate'
    if r.get('issue'): return 'Needs review'
    try: days=(date.fromisoformat(r['expiry'])-(today or date.today())).days
    except (ValueError,KeyError): return 'Needs review'
    if days < 0: return 'Expired'
    if days <= 15: return 'Within 15 days'
    if days <= 30: return '16–30 days'
    return 'Over 30 days'

def deduplicate(rows):
    seen={}; dates={}
    for r in rows:
        r.pop('duplicate',None)
        if not r.get('issue'):
            key=(r['permit'],r['iqama'])
            dates.setdefault(key,set()).add(r['expiry'])
    for r in rows:
        key=(r.get('permit'),r.get('iqama'))
        if not r.get('issue') and len(dates.get(key,[]))>1:
            r['issue']='Conflicting expiry dates for the same permit; check both documents'
        if not r.get('issue'):
            key=(r['permit'],r['iqama'],r['expiry'])
            if key in seen: r['duplicate']=True
            else: seen[key]=r
    return rows

def ocr_executable():
    return BASE/'ocr'/('tesseract.exe' if sys.platform == 'win32' else 'tesseract')

def open_item(path):
    if sys.platform == 'darwin':
        subprocess.Popen(['/usr/bin/open', str(path)])
    elif sys.platform == 'win32':
        os.startfile(str(path))
    else:
        subprocess.Popen(['xdg-open', str(path)])

def ocr_page(page):
    exe=ocr_executable()
    if not exe.exists(): raise RuntimeError('Bundled scan reader is missing')
    with tempfile.TemporaryDirectory(prefix='ajeer-') as folder:
        image=Path(folder)/'page.png'
        bitmap=page.render(scale=2.5)
        bitmap.to_pil().save(image); bitmap.close()
        result=subprocess.run([str(exe),str(image),'stdout','--tessdata-dir',str(BASE/'ocr'/'tessdata'),'-l','ara+eng','--psm','3'],capture_output=True,timeout=90,creationflags=0x08000000 if sys.platform == 'win32' else 0)
        if result.returncode: raise RuntimeError('Scan reader could not read this page')
        return result.stdout.decode('utf-8',errors='replace')

def read_pdf(path, digest, progress=lambda s:None):
    rows=[]
    with pdfium.PdfDocument(str(path)) as doc:
        if len(doc)==0: raise ValueError('PDF has no pages')
        for i in range(len(doc)):
            progress(path.name+' — page '+str(i+1))
            page=doc[i]
            try:
                tp=page.get_textpage()
                try: text=tp.get_text_range()
                finally: tp.close()
                r=extract(text,path.name)
                # Supporting pages are still listed for review, never silently dropped.
                if r['issue']:
                    try:
                        scanned=extract(ocr_page(page),path.name)
                        if not scanned['issue'] or len(compact(text))<60:
                            r=scanned
                            r['method']='Scan (OCR)'
                            r['issue']=('Confirm the scanned name, Iqama, permit and expiry before counting. '+r['issue']).strip()
                    except Exception as e:
                        r['issue']+='; '+str(e)
                r.update(file=str(path),page=i+1,key=digest+':'+str(i+1))
                rows.append(r)
            finally: page.close()
    return rows

def desktop():
    if sys.platform == 'darwin':
        return Path.home()/'Desktop'
    # Windows resolves redirected and OneDrive Desktops through its known folder.
    buf=ctypes.create_unicode_buffer(32768)
    if ctypes.windll.shell32.SHGetFolderPathW(None,0x10,None,0,buf)!=0:
        raise RuntimeError('Windows Desktop folder could not be located')
    return Path(buf.value)

def atomic_json(path,data):
    temp=path.with_suffix('.tmp')
    temp.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    os.replace(temp,path)

class App:
    def __init__(self, root, folder=None, smoke=False):
        self.root=root; self.smoke=smoke
        self.folder=Path(folder) if folder else desktop()/'Ajeer Documents'
        self.folder.mkdir(parents=True,exist_ok=True)
        self.data=self.folder/'.ajeer-tracker'; self.data.mkdir(exist_ok=True)
        self.override_path=self.data/'confirmed-dates.json'
        self.overrides={}; self.cache={}; self.rows=[]; self.view=[]; self.busy=False; self.q=queue.Queue()
        for path,attr in [(self.override_path,'overrides'),(self.data/'cache.json','cache')]:
            if path.exists():
                try: setattr(self,attr,json.loads(path.read_text(encoding='utf-8')))
                except Exception:
                    messagebox.showwarning('Saved data needs attention',str(path)+' could not be read. Original PDFs are safe. The folder will be rescanned.')
        root.title('Ajeer Expiry Tracker'); root.geometry('1180x740'); root.minsize(1000,640); root.configure(bg=COLORS['bg'])
        style=ttk.Style(); style.theme_use('clam')
        style.configure('TFrame',background=COLORS['bg']); style.configure('TLabel',background=COLORS['bg'],foreground=COLORS['ink'],font=('Segoe UI',10))
        style.configure('TButton',font=('Segoe UI',10),padding=(12,8)); style.configure('Treeview',font=('Segoe UI',10),rowheight=34)
        style.configure('Treeview.Heading',font=('Segoe UI Semibold',10),padding=8)
        body=ttk.Frame(root,padding=22); body.pack(fill='both',expand=True)
        ttk.Label(body,text='Ajeer expiry tracker',font=('Segoe UI Semibold',25)).pack(anchor='w')
        ttk.Label(body,text='Put PDFs in your Desktop folder. Counts refresh automatically while this app is open.',foreground=COLORS['muted']).pack(anchor='w',pady=(4,16))
        bar=ttk.Frame(body);bar.pack(fill='x')
        for label,fn in [('Open documents folder',self.open_folder),('Add PDFs',self.add_files),('Refresh now',self.scan),('Export displayed list',self.export),('Help',self.help)]:
            ttk.Button(bar,text=label,command=fn).pack(side='left',padx=(0,8))
        self.status=tk.StringVar(value='Ready'); ttk.Label(body,textvariable=self.status,foreground=COLORS['muted']).pack(anchor='w',pady=(12,6))
        cards=ttk.Frame(body);cards.pack(fill='x',pady=(0,15)); self.cards={}
        for title,color in [('Expired','#a72838'),('Within 15 days','#b75812'),('Within 30 days','#846200'),('Over 30 days','#147254'),('Needs review','#7051a0')]:
            frame=tk.Frame(cards,bg='white',padx=12,pady=12);frame.pack(side='left',fill='x',expand=True,padx=(0,8))
            value=tk.Label(frame,text='0',font=('Segoe UI Semibold',26),fg=color,bg='white');value.pack(anchor='w')
            tk.Label(frame,text=title,font=('Segoe UI',10),fg=COLORS['ink'],bg='white').pack(anchor='w')
            self.cards[title]=value
            for widget in (frame,value): widget.bind('<Button-1>',lambda e,t=title:self.set_filter(t))
        ttk.Label(body,text='Within 30 days includes the 15-day total. Expired permits and review items are excluded from both.',foreground=COLORS['muted']).pack(anchor='w')
        filters=ttk.Frame(body);filters.pack(fill='x',pady=12)
        self.query=tk.StringVar(); self.filter=tk.StringVar(value='All')
        ttk.Label(filters,text='Search').pack(side='left'); entry=ttk.Entry(filters,textvariable=self.query,width=34);entry.pack(side='left',padx=8)
        combo=ttk.Combobox(filters,textvariable=self.filter,state='readonly',values=['All','Expired','Within 15 days','Within 30 days','16–30 days','Over 30 days','Needs review','Duplicate'],width=22);combo.pack(side='left',padx=8)
        self.query.trace_add('write',lambda *a:self.render());combo.bind('<<ComboboxSelected>>',lambda e:self.render())
        panel=ttk.Frame(body);panel.pack(fill='both',expand=True)
        cols=('name','iqama','permit','expiry','days','status','source')
        self.tree=ttk.Treeview(panel,columns=cols,show='headings',selectmode='browse')
        for col,title,width in zip(cols,['Worker / document','Iqama','Permit','Expiry','Days left','Status','PDF / page'],[220,115,115,105,75,140,230]):
            self.tree.heading(col,text=title);self.tree.column(col,width=width,minwidth=60,stretch=col in ('name','source'))
        y=ttk.Scrollbar(panel,orient='vertical',command=self.tree.yview);x=ttk.Scrollbar(panel,orient='horizontal',command=self.tree.xview)
        self.tree.configure(yscrollcommand=y.set,xscrollcommand=x.set)
        self.tree.grid(row=0,column=0,sticky='nsew');y.grid(row=0,column=1,sticky='ns');x.grid(row=1,column=0,sticky='ew');panel.rowconfigure(0,weight=1);panel.columnconfigure(0,weight=1)
        for tag,color in [('Expired','#fce7ea'),('Within 15 days','#fff0df'),('16–30 days','#fff8d9'),('Needs review','#eee8fa'),('Duplicate','#e7ebf0')]:self.tree.tag_configure(tag,background=color)
        self.tree.bind('<Double-1>',lambda e:self.open_selected()); self.tree.bind('<<TreeviewSelect>>',self.details)
        foot=ttk.Frame(body);foot.pack(fill='x',pady=(12,0))
        ttk.Button(foot,text='Open selected PDF',command=self.open_selected).pack(side='left')
        ttk.Button(foot,text='Review / correct selected',command=self.review).pack(side='left',padx=8)
        self.detail=tk.StringVar(value='Select a row to see its reading notes.');ttk.Label(body,textvariable=self.detail,wraplength=1080,foreground=COLORS['muted']).pack(anchor='w',pady=(8,0))
        # Reserve space for the action buttons and notes before the expanding table.
        panel.pack_forget();panel.pack(fill='both',expand=True,pady=(8,0))
        root.after(150,self.poll)
        if not smoke: root.after(250,self.scan);root.after(30000,self.auto_scan)

    def set_filter(self,t):self.filter.set(t);self.render()
    def open_folder(self):open_item(self.folder)
    def selected(self):
        s=self.tree.selection()
        return self.view[int(s[0])] if s else None
    def details(self,e=None):
        r=self.selected()
        if r:self.detail.set(r.get('issue') or (r.get('method','PDF text')+' · '+r['file']+' · page '+str(r['page'])+(' · duplicate excluded from counts' if r.get('duplicate') else '')))
    def open_selected(self):
        r=self.selected()
        if r:
            try:open_item(r['file'])
            except Exception as e:messagebox.showerror('Cannot open PDF',str(e))
    def add_files(self):
        import shutil
        files=filedialog.askopenfilenames(title='Add Ajeer PDFs',filetypes=[('PDF documents','*.pdf')])
        for src in files:
            target=self.folder/Path(src).name
            if Path(src).resolve()==target.resolve():continue
            if target.exists():
                n=2
                while target.exists():target=self.folder/(Path(src).stem+' ('+str(n)+').pdf');n+=1
            try:shutil.copy2(src,target)
            except Exception as e:messagebox.showerror('Copy failed',str(e))
        if files:self.scan()
    def auto_scan(self):self.scan();self.root.after(30000,self.auto_scan)
    def scan(self):
        if self.busy:return
        self.busy=True;self.status.set('Checking the documents folder…')
        threading.Thread(target=self.worker,daemon=True).start()
    def worker(self):
        rows=[];cache={};errors=[]
        try:
            def walk_error(e):errors.append(str(e))
            files=[]
            for folder,dirs,names in os.walk(self.folder,onerror=walk_error):
                dirs[:]=sorted(d for d in dirs if d!='.ajeer-tracker' and not Path(folder,d).is_symlink())
                files.extend(Path(folder,n) for n in sorted(names) if not n.startswith('~$'))
            for path in files:
                try:
                    if path.is_symlink():continue
                    if path.suffix.lower()!='.pdf':
                        rows.append(dict(name=path.name,file=str(path),page=0,key='unsupported:'+str(path),issue='Not a PDF. Export this document to PDF and place the PDF here.',expiry='',permit='',iqama='',method='Unsupported file'));continue
                    digest=hashlib.sha256(path.read_bytes()).hexdigest()
                    if digest in self.cache:items=[dict(r) for r in self.cache[digest]]
                    else:items=read_pdf(path,digest,lambda s:self.q.put(('progress','Reading '+s)))
                    cache[digest]=[dict(r) for r in items]
                    for r in items:
                        r['file']=str(path)
                        if r['key'] in self.overrides:
                            r.update(self.overrides[r['key']]);r['method']='Manually confirmed';r['issue']=''
                        rows.append(r)
                except Exception as e:rows.append(dict(name=path.name,file=str(path),page=0,key='error:'+str(path),issue='Could not read PDF: '+str(e),expiry='',permit='',iqama='',method='Read error'))
            atomic_json(self.data/'cache.json',cache)
            self.q.put(('done',(deduplicate(rows),cache,errors)))
        except Exception as e:self.q.put(('error',str(e)))
    def poll(self):
        try:
            while True:
                kind,value=self.q.get_nowait()
                if kind=='progress':self.status.set(value)
                elif kind=='done':
                    self.rows,self.cache,errors=value;self.busy=False;self.render()
                    self.status.set('Updated '+datetime.now().strftime('%d %b %Y, %H:%M')+' · '+str(len({r['file'] for r in self.rows}))+' files · '+str(len(self.rows))+' page records · '+str(sum(bool(r.get('duplicate')) for r in self.rows))+' duplicates excluded'+(' · FOLDER ACCESS ERROR: '+ '; '.join(errors) if errors else ''))
                else:self.busy=False;self.status.set('Scan failed: '+value)
        except queue.Empty:pass
        self.root.after(150,self.poll)
    def render(self):
        today=date.today(); counts={}
        for r in self.rows:
            b=bucket(r,today);counts[b]=counts.get(b,0)+1
        for title,widget in self.cards.items():widget.config(text=str(counts.get(title,0)+(counts.get('16–30 days',0) if title=='Within 30 days' else 0)))
        q=self.query.get().lower();f=self.filter.get();self.view=[]
        self.tree.delete(*self.tree.get_children())
        for r in sorted(self.rows,key=lambda r:(r.get('expiry') or '0000',r.get('name',''))):
            b=bucket(r,today)
            if f!='All' and not (b==f or f=='Within 30 days' and b in ('Within 15 days','16–30 days')):continue
            if q and q not in ' '.join(str(v) for v in r.values()).lower():continue
            days=(date.fromisoformat(r['expiry'])-today).days if r.get('expiry') else ''
            i=len(self.view);self.view.append(r)
            self.tree.insert('', 'end',iid=str(i),values=(r.get('name',''),r.get('iqama',''),r.get('permit',''),r.get('expiry',''),days,b,Path(r['file']).name+' / p'+str(r['page'])),tags=(b,))
    def review(self):
        r=self.selected()
        if not r:return
        if 'multiple permit' in r.get('issue','').lower() or 'multiple iqama' in r.get('issue','').lower():
            messagebox.showinfo('Separate permits required','This page may contain multiple permits or identities. Save each permit as a separate PDF page and refresh, so every permit is tracked.');return
        if not r['page']:messagebox.showinfo('PDF required','This file could not be read. Put a readable PDF in the folder, then refresh.');return
        win=tk.Toplevel(self.root);win.title('Check against the original PDF');win.geometry('540x440');win.transient(self.root);win.grab_set()
        ttk.Label(win,text='Open the PDF and confirm these four fields.',wraplength=480).pack(pady=14)
        ttk.Button(win,text='Open original PDF',command=lambda:open_item(r['file'])).pack()
        fields={}
        for key,label in [('name','Worker name'),('iqama','Iqama (10 digits)'),('permit','Permit number (TQ…)'),('expiry','Expiry date (YYYY-MM-DD)')]:
            row=ttk.Frame(win,padding=8);row.pack(fill='x');ttk.Label(row,text=label,width=27).pack(side='left');v=tk.StringVar(value=r.get(key,''));ttk.Entry(row,textvariable=v,width=30).pack(side='left');fields[key]=v
        def save():
            v={k:x.get().strip() for k,x in fields.items()};v['permit']=v['permit'].upper()
            if not v['name'] or not re.fullmatch('[12][0-9]{9}',v['iqama']) or not re.fullmatch('TQ[0-9]{5,12}',v['permit']) or not re.fullmatch(r'20\d{2}-\d{2}-\d{2}',v['expiry']) or parse_date(v['expiry'])!=v['expiry']:
                messagebox.showerror('Check the fields','Enter a name, valid 10-digit Iqama, TQ permit number and a Gregorian date as YYYY-MM-DD.',parent=win);return
            self.overrides[r['key']]=v
            try:atomic_json(self.override_path,self.overrides)
            except Exception as e:messagebox.showerror('Could not save',str(e),parent=win);return
            win.destroy();self.scan()
        ttk.Button(win,text='Confirm and save',command=save).pack(pady=12)
        ttk.Label(win,text='Confirmation applies only to this exact PDF content and page.\nReplacing the PDF requires a fresh reading.',wraplength=480).pack()
    def export(self):
        path=filedialog.asksaveasfilename(title='Export displayed rows',defaultextension='.csv',initialfile='Ajeer expiry '+date.today().isoformat()+'.csv',filetypes=[('Excel-compatible CSV','*.csv')])
        if not path:return
        try:
            with open(path,'w',newline='',encoding='utf-8-sig') as f:
                w=csv.writer(f);w.writerow(['As of','Worker','Iqama','Permit','Expiry','Days left','Status','Reading method','Notes','PDF','Page'])
                for r in self.view:
                    values=[date.today().isoformat(),r.get('name',''),r.get('iqama',''),r.get('permit',''),r.get('expiry',''),(date.fromisoformat(r['expiry'])-date.today()).days if r.get('expiry') else '',bucket(r),r.get('method',''),r.get('issue',''),r['file'],r['page']]
                    w.writerow(["'"+v if isinstance(v,str) and v.lstrip().startswith(('=','+','-','@')) else v for v in values])
            messagebox.showinfo('Export complete','Saved '+str(len(self.view))+' displayed rows.')
        except Exception as e:messagebox.showerror('Export failed',str(e))
    def help(self):messagebox.showinfo('How to use Ajeer Tracker','1. Put PDFs in Desktop > Ajeer Documents, or use Add PDFs. Subfolders are included.\n\n2. Leave this app open for automatic checking every 30 seconds. It checks again whenever opened.\n\n3. Click a total to filter. Within 30 days includes Within 15 days. Today is included in Within 15 days; yesterday is Expired. Dates use this computer clock.\n\n4. Scans and uncertain pages stay in Needs review until you check and confirm. Supporting pages also remain visible for review.\n\n5. Exact permit + Iqama + expiry duplicates count once. Different permit numbers count separately. Conflicting dates need review.\n\n6. Export saves the displayed list as CSV for Excel.\n\nEverything stays on this computer. No internet, Word, Excel or Python installation is required. Back up the entire Ajeer Documents folder, including .ajeer-tracker. This tracks document dates; it does not verify permits with the issuer.')

def main():
    if '--runtime-check' in sys.argv:
        exe=ocr_executable()
        results={}
        for flag in ('--version','--list-langs'):
            p=subprocess.run([str(exe), flag, '--tessdata-dir',str(BASE/'ocr'/'tessdata')],capture_output=True,text=True,timeout=30,creationflags=0x08000000 if sys.platform=='win32' else 0)
            if p.returncode: raise RuntimeError('OCR runtime failed: '+p.stderr)
            results[flag]=p.stdout
        if not all(lang in results['--list-langs'].split() for lang in ['ara','eng']): raise RuntimeError('Arabic or English OCR data missing')
        results['pdf_library']=pdfium.PdfDocument.new().__class__.__name__
        Path(sys.argv[sys.argv.index('--runtime-check')+1]).write_text(json.dumps(results),encoding='utf-8')
        return
    if '--scan-test' in sys.argv:
        folder=Path(sys.argv[sys.argv.index('--scan-test')+1]);rows=[]
        for p in sorted(folder.glob('*.pdf')):rows.extend(read_pdf(p,hashlib.sha256(p.read_bytes()).hexdigest()))
        result=json.dumps(deduplicate(rows),ensure_ascii=True)
        if len(sys.argv)>sys.argv.index('--scan-test')+2:
            Path(sys.argv[sys.argv.index('--scan-test')+2]).write_text(result,encoding='utf-8')
        elif sys.stdout:print(result)
        return
    root=tk.Tk()
    if '--smoke-ui' in sys.argv:
        folder=sys.argv[sys.argv.index('--smoke-ui')+1]
        app=App(root,folder,True);root.update();assert root.winfo_viewable();assert len(app.cards)==5
        Path(folder,'ui-smoke-ok.txt').write_text('Window created; five cards, PDF table and controls initialized.',encoding='utf-8');root.destroy();return
    folder=sys.argv[sys.argv.index('--test-folder')+1] if '--test-folder' in sys.argv else None
    App(root,folder);root.mainloop()

if __name__=='__main__':
    try:main()
    except Exception:
        log=Path(tempfile.gettempdir())/'AjeerTracker-error.txt';log.write_text(traceback.format_exc(),encoding='utf-8')
        if any(flag in sys.argv for flag in ('--scan-test','--smoke-ui','--runtime-check')):raise
        messagebox.showerror('Ajeer Tracker could not start','Details saved to '+str(log))
