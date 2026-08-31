# Task brief
Goal: Implement slugify(text): lowercase the input, replace every run of non-alphanumeric characters with a single hyphen, and strip leading/trailing hyphens.
Function name: `slugify`

Visible test cases the implementation must pass:
- `slugify('Hello World') == 'hello-world'`
- `slugify('  Hello,   World!  ') == 'hello-world'`
- `slugify('Python_3.12 rocks') == 'python-3-12-rocks'`

## Output contract
Write the complete, self-contained implementation to `candidate.py`
in this directory, then exit. Do not claim success yourself —
an external verifier is the only judge of the artifact.
