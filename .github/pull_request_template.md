## Summary

What changed and why?

## Verification

- [ ] `python scripts/verify_release.py`
- [ ] Screenshots or docs updated if the UI/workflow changed
- [ ] No real secrets, private paths, private project memory, or account screenshots

## Security Review

- [ ] No raw provider keys are rendered, logged, exported, or committed
- [ ] Cloud/provider calls remain explicit and approval-gated where needed
- [ ] New provider behavior documents auth, rate limit, model mismatch, and remote error handling

## Notes

Anything reviewers should know?
