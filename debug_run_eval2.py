from pathlib import Path
import shutil
from code_agent.evals import builtin_fixture_eval_cases, _make_fixture_agent, _write_fixture_files, ScriptedModel
from code_agent.tools import ToolRegistry

case = next(c for c in builtin_fixture_eval_cases() if c.name == 'recover_after_failed_verification')

# Create workspace dir
workspace = Path('debug_workspace')
if workspace.exists():
    shutil.rmtree(workspace)
workspace.mkdir()

# write files
_write_fixture_files(workspace, case.files)

# model + tools + agent
model = ScriptedModel(list(case.responses))
tools = ToolRegistry(workspace=workspace, dry_run=False, approval_callback=case.approval_policy)
agent = _make_fixture_agent(workspace, model, tools, max_steps=case.max_steps, max_failures=case.max_failures)

result = agent.run_detailed(case.task)
print('Message:', result.message)
print('Changed paths:', result.changed_paths)
print('Mutation records:', result.mutation_records)
print('Command records:', result.command_records)
print('Verification results:', result.verification_results)
print('Context records:', result.context_records)
print('Model messages seen count:', len(model.messages_seen))
for i, msgs in enumerate(model.messages_seen):
    print('Model call', i)
    for m in msgs:
        print(' ', m)

# show workspace file
print('\nmathlib.py content:\n')
print((workspace / 'mathlib.py').read_text())

# cleanup
shutil.rmtree(workspace)
