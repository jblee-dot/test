import requests
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from models import Round, Participant, Transaction
import datetime

# 고정 입금 주소 (모든 참가자는 이 주소로 송금)
FIXED_DEPOSIT_ADDRESS = "bc1qsingleaddress1234567890"
# 운영팀 수수료 주소 (라운드 종료 시 운영팀이 수수료를 받는 주소)
ADMIN_FEE_ADDRESS = "bc1qadminfeeaddress1234567890"

# 상금 및 운영비 비율
PRIZE_RATE = 0.99
ADMIN_RATE = 0.01
# 참가자 1명당 금액(BTC) 및 전체 라운드 금액(10명 기준)
PARTICIPANT_BTC_AMOUNT = 0.1
TOTAL_AMOUNT = PARTICIPANT_BTC_AMOUNT * 10

def get_latest_block_hash():
    """
    Blockstream API를 사용해 최신 비트코인 블록의 해시를 가져온다.
    """
    try:
        resp = requests.get("https://blockstream.info/api/blocks/tip/hash")
        if resp.status_code == 200:
            return resp.text.strip()
        else:
            print(f"블록 해시 조회 실패: {resp.status_code}")
            return None
    except Exception as e:
        print(f"블록 해시 조회 중 오류: {e}")
        return None

def get_winner_index(block_hash, num_participants):
    """
    block_hash(16진수 문자열)와 참가자 수를 받아서, 0~num_participants-1 중 하나의 인덱스를 반환
    """
    if not block_hash or num_participants == 0:
        return None
    idx = int(block_hash, 16) % num_participants
    return idx

def record_payout_transactions(session, winner_address):
    """
    상금(99%)과 운영비(1%) 트랜잭션을 DB에 기록 (status는 'pending')
    """
    prize_amount = round(TOTAL_AMOUNT * PRIZE_RATE, 8)
    admin_fee = round(TOTAL_AMOUNT * ADMIN_RATE, 8)

    # 상금 트랜잭션 기록
    prize_tx = Transaction(
        txid=None,  # 실제 출금시 서명 후 채움
        type="payout",
        status="pending",
        amount=prize_amount,
        address=winner_address,
        timestamp=datetime.datetime.utcnow()
    )
    # 운영비 트랜잭션 기록
    admin_tx = Transaction(
        txid=None,  # 실제 출금시 서명 후 채움
        type="fee",
        status="pending",
        amount=admin_fee,
        address=ADMIN_FEE_ADDRESS,
        timestamp=datetime.datetime.utcnow()
    )
    session.add(prize_tx)
    session.add(admin_tx)
    session.commit()
    print(f"상금/운영비 트랜잭션 기록: {prize_amount} BTC → {winner_address}, {admin_fee} BTC → {ADMIN_FEE_ADDRESS}")

def create_next_round(session):
    """
    차기 라운드를 자동 생성한다. 입금 주소는 항상 고정(FIXED_DEPOSIT_ADDRESS)
    """
    new_round = Round(
        deposit_address=FIXED_DEPOSIT_ADDRESS,
        status="open",
        opened_at=datetime.datetime.utcnow()
    )
    session.add(new_round)
    session.commit()
    print(f"[차기 라운드 자동생성] 입금주소: {FIXED_DEPOSIT_ADDRESS}, 라운드ID: {new_round.id}")
    return new_round.id

def close_round(round_id, db_url="sqlite:///lotto.db"):
    """
    - 참가자 10명 도달 시 라운드를 마감(closed), 마감시간 기록
    - 블록해시 기반 무작위 추첨
    - 당첨자/운영비 정산 트랜잭션 기록
    - 차기 라운드 자동 생성(고정 입금 주소)
    """
    engine = create_engine(db_url)
    Session = sessionmaker(bind=engine)
    session = Session()

    rnd = session.query(Round).filter_by(id=round_id, status='open').first()
    if not rnd:
        print("마감할 수 있는 오픈 상태 라운드가 없습니다.")
        session.close()
        return

    participants = session.query(Participant).filter_by(round_id=round_id).order_by(Participant.created_at).all()
    if len(participants) < 10:
        print(f"참가자가 {len(participants)}명에 불과해 마감 불가.")
        session.close()
        return

    # 참가자 순번 부여
    for idx, p in enumerate(participants[:10], 1):
        p.round_index = idx
    session.commit()

    # 블록해시 기반 추첨
    block_hash = get_latest_block_hash()
    if not block_hash:
        print("블록 해시를 불러올 수 없어 추첨 불가.")
        session.close()
        return
    winner_idx = get_winner_index(block_hash, 10)
    winner_participant = participants[winner_idx]
    winner_address = winner_participant.btc_address

    # 라운드 정보 마감
    rnd.status = "closed"
    rnd.closed_at = datetime.datetime.utcnow()
    rnd.winner = winner_address
    session.commit()

    print(f"라운드 {round_id} 마감! 당첨자: {winner_address}")
    print(f"추첨 블록 해시: {block_hash}")

    # 정산 트랜잭션 기록(상금/운영비)
    record_payout_transactions(session, winner_address)

    print("참가자 명단 (순번):")
    for p in participants[:10]:
        is_winner = " (당첨)" if p.btc_address == winner_address else ""
        print(f"{p.round_index}번 - {p.btc_address}{is_winner}")

    # 차기 라운드 자동생성(고정 주소)
    create_next_round(session)

    session.close()

if __name__ == "__main__":
    close_round(1)