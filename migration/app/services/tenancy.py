from sqlalchemy.orm import Session

from app.core.security import Principal, hash_password
from app.db.models import Membership, Organization, User, new_id


def ensure_principal_records(db: Session, principal: Principal) -> None:
    """Creates local development identities; production identities normally sync from IdP."""
    if db.get(Organization, principal.organization_id) is None:
        db.add(Organization(id=principal.organization_id, name=principal.organization_id))
    if db.get(User, principal.user_id) is None:
        db.add(User(id=principal.user_id))
    membership = db.get(
        Membership,
        {
            "organization_id": principal.organization_id,
            "user_id": principal.user_id,
        },
    )
    if membership is None:
        db.add(
            Membership(
                organization_id=principal.organization_id,
                user_id=principal.user_id,
                role="owner",
            )
        )
    db.commit()


def seed_test_account(db: Session, email: str, password: str, organization_name: str) -> None:
    """Creates a fixed test login for local/Docker environments; skips if it already exists."""
    email = email.lower()
    if db.query(User).filter(User.email == email).first() is not None:
        return
    organization = Organization(id=new_id(), name=organization_name)
    user = User(id=new_id(), email=email, password_hash=hash_password(password))
    db.add(organization)
    db.add(user)
    db.flush()
    db.add(Membership(organization_id=organization.id, user_id=user.id, role="owner"))
    db.commit()

