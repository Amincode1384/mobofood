import os, subprocess, json, sys

root=r"C:\CLAUDE\MOBO-food - 1 - Copy\Robotic-Restaurant-main"
parent_dir=os.path.dirname(root)
payload_path=os.path.join(parent_dir, 'tree_payload.json')

# Top-level scratch/generated files to never push.
exclude_files = {
    'server.pid','db.sqlite3','server.log','server_run.log','notify.ps1',
    'BEGINNER_GUIDE.md','EnjiNex_Checklist.docx','MISSION_API_DOCS.md',
    'project-activity-history.docx','project_work_report.docx','qr_table_2.png',
    'kitchen-board-5-columns.png','.arena_task.md','.coverage',
    # tmp diagnostics
    'tmp_check_mgr.py','tmp_check_mission_block_end.py','tmp_check_mission_statuses.py',
    'tmp_check_mission_statuses2.py','tmp_check_models.py','tmp_check_ready_order.py',
    'tmp_check_validator.py','tmp_new_mission_block.py','tmp_show_models_ends.py',
    # logs
    'dev_server.log',
}

# Directories to never recurse into.
exclude_dirs = {'.git','.arena','.claude','.idea','.vibe-wise','.freebuff','__pycache__',
                'media','venv','.venv','env','node_modules'}

# Do not include the nested copy of the project, or the helper script dir itself.
exclude_rel_prefixes = (
    'Robotic-Restaurant-main/',
    'scripts/',
)

entries=[]
seen=set()
errors=[]

def log(s):
    print(s, file=sys.stderr)

for dirpath, dirnames, filenames in os.walk(root):
    base=os.path.basename(dirpath)
    if base in exclude_dirs:
        dirnames.clear()
        continue
    for fn in filenames:
        full=os.path.join(dirpath,fn)
        rel=os.path.relpath(full,root).replace(os.sep,'/')
        if fn in exclude_files:
            continue
        if any(rel.startswith(p) for p in exclude_rel_prefixes):
            continue
        if fn=='__init__.py' and base=='__pycache__':
            continue
        if fn.endswith('.pyc'):
            continue
        if fn.endswith('.png') and not rel.startswith('static/'):
            continue
        if rel in seen:
            continue
        seen.add(rel)
        try:
            sha=subprocess.check_output(['git','hash-object','-w',full],cwd=root).decode().strip()
        except subprocess.CalledProcessError as e:
            errors.append((rel, str(e)))
            continue
        entries.append({'path':rel,'mode':'100644','type':'blob','sha':sha})

payload={'tree':entries}
with open(payload_path,'w',encoding='utf-8') as f:
    json.dump(payload,f,ensure_ascii=False)

print('PLANNED_COUNT',len(entries),file=sys.stderr)
print('OUT_PATH',payload_path,file=sys.stderr)
print('OUT_SIZE',os.path.getsize(payload_path),file=sys.stderr)
if errors:
    print('ERRORS',len(errors),file=sys.stderr)
    for rel,msg in errors[:20]:
        print('ERR',rel,msg,file=sys.stderr)
else:
    print('NO_ERRORS',file=sys.stderr)
print('DONE_LISTING',file=sys.stderr)
