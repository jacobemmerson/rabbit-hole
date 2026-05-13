Run the test suite and map results against the specific test cases listed in the README, flagging tests that are failing and test cases that have no implementation at all.

**Usage:** `/test-coverage`

## Steps

1. Find the test runner configuration (pytest.ini, pyproject.toml, setup.cfg) and identify the test directory.

2. Run the full test suite:
   ```bash
   pytest --tb=short -q
   ```

3. Collect the full list of canonical test cases from the README across all three phases (see phase-check command for the complete list).

4. For each canonical test case:
   - **Covered & passing** — a test exists and passed
   - **Covered & failing** — a test exists but failed (show the error)
   - **Missing** — no test in the suite corresponds to this case

5. Map test function names to README test cases by reading the test files and matching intent (don't require exact name matching — use judgment).

6. Output a summary table:
   | Status | Count |
   |--------|-------|
   | Passing | N |
   | Failing | N |
   | Missing | N |

7. List all failing tests with their error output.

8. List all missing test cases grouped by phase, in the order they appear in the README.

9. Suggest which missing test cases to write next based on what's already implemented.
