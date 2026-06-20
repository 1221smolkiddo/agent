from code_agent.evals import builtin_fixture_eval_cases, run_fixture_eval
case = next(c for c in builtin_fixture_eval_cases() if c.name == 'recover_after_failed_verification')
ok, detail = run_fixture_eval(case)
print('OK=', ok)
print('DETAIL=', detail)
