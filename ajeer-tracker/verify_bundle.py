"""Fail a Mac release if a bundled Mach-O binary requires Homebrew paths."""
import subprocess,sys
from pathlib import Path
app=Path(sys.argv[1]);errors=[]
for p in app.rglob('*'):
    if not p.is_file() or p.is_symlink():continue
    try:kind=subprocess.check_output(['/usr/bin/file','-b',str(p)],text=True)
    except subprocess.CalledProcessError:continue
    if 'Mach-O' not in kind:continue
    links=subprocess.check_output(['/usr/bin/otool','-L',str(p)],text=True)
    for line in links.splitlines()[1:]:
        dep=line.strip().split(' (')[0]
        if dep.startswith(('/opt/homebrew/','/usr/local/','/Library/Frameworks/Python.framework/')):
            errors.append(str(p.relative_to(app))+': '+dep)
if errors:raise SystemExit('Unbundled dependencies:\n'+'\n'.join(errors))
print('PASS: no external Homebrew or Python-framework dependencies.')
