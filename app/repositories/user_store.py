"""사용자 메모리 저장소 (저장소 계층)

FastAPI 실습용 사용자 데이터를 **파이썬 리스트 하나**에 담아 둔다.
DB 가 아니라 메모리라서 **서버를 끄면 추가한 사용자도 함께 사라진다.**
(KRX 시세는 양이 많아 SQLite 를 쓰지만, 이쪽은 30건짜리 실습 데이터라 리스트로 충분하다.)

라우터(`app/routers/user_router.py`)는 이 모듈의 함수만 부르고 리스트를 직접 만지지 않는다.
나중에 진짜 DB 로 바꿔도 **이 파일만 고치면** 되도록 하기 위함이다.
"""

# Mock 사용자의 나이를 무작위로 만들기 위해 사용
import random
from typing import Dict, List, Optional

MOCK_USER_COUNT = 30            # 서버 시작 시 만들어 두는 Mock 사용자 수
MOCK_AGE_RANGE = (18, 60)       # Mock 나이 범위 (양끝 포함)


def generate_mock_users() -> List[Dict]:
    """user1 ~ user30 을 딕셔너리 리스트로 만들어 반환한다."""
    users = []                                     # 결과를 담을 빈 리스트
    for i in range(1, MOCK_USER_COUNT + 1):        # 1 부터 30 까지
        users.append({
            "id": i,                               # ID 는 1부터 순서대로
            "username": f"user{i}",                # f-string 으로 번호를 끼워 넣는다
            "email": f"user{i}@example.com",
            "age": random.randint(*MOCK_AGE_RANGE),
        })
    return users


# 모듈이 import 될 때 딱 한 번 실행된다. 이후 모든 요청이 이 리스트를 공유한다.
# `age` 는 무작위라 서버를 재시작할 때마다 값이 달라진다.
_users: List[Dict] = generate_mock_users()


def list_users() -> List[Dict]:
    """저장된 사용자 전체를 반환한다."""
    return _users


def find_user(user_id: int) -> Optional[Dict]:
    """`user_id` 로 사용자 1명을 찾는다. 없으면 `None`.

    리스트를 처음부터 훑는 선형 탐색이다. 30건이라 문제없지만 실제 서비스라면 DB 인덱스를 쓴다.
    """
    for user in _users:
        if user["id"] == user_id:
            return user                            # 찾는 순간 반환하고 함수 종료
    return None


def add_user(username: str, email: str, age: Optional[int] = None) -> Dict:
    """새 사용자를 추가하고 저장된 값을 그대로 반환한다.

    `id` 는 `현재 개수 + 1` 로 붙인다. 삭제 기능이 생기면 ID 가 겹칠 수 있는 방식이라
    실제 서비스에서는 DB 의 auto increment 나 UUID 를 쓴다.
    """
    user = {
        "id": len(_users) + 1,
        "username": username,
        "email": email,
        "age": age,                                # 요청에서 생략했다면 None 이 들어간다
    }
    _users.append(user)
    return user
