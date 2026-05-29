"""Claude Code Skill 진입점용 어댑터 레이어.

`cli.py`(argparse 진입점)에서 13개 서브커맨드를 dispatch할 수 있도록
`tools/*` 모듈과 `server.py`의 검증된 헬퍼를 얇게 감싼다. `server.py`는
수정하지 않고 import만 한다.
"""
