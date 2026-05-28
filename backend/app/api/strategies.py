import logging
from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.api.auth import get_current_user
from app.models.trading import User, Strategy
from app.schemas import StrategyCreate, StrategyEdit, StrategyOut

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

router = APIRouter(prefix="/strategies", tags=["Trading Strategies"])

@router.post("/create", response_model=StrategyOut, status_code=status.HTTP_201_CREATED)
def create_strategy(
    strat_in: StrategyCreate, 
    current_user: User = Depends(get_current_user), 
    db: Session = Depends(get_db)
):
    """
    Saves a new automated F&O options trading strategy (e.g. Time Formula 35pts).
    Linked directly to the authenticated JWT user.
    """
    db_strat = Strategy(
        **strat_in.model_dump(),
        owner_id=current_user.id,
        is_active=False,
        # Default initialization values
        ce_reference_close=None,
        ce_current_qty_lots=strat_in.base_qty_lots,
        ce_state="IDLE",
        pe_reference_close=None,
        pe_current_qty_lots=strat_in.base_qty_lots,
        pe_state="IDLE"
    )
    db.add(db_strat)
    db.commit()
    db.refresh(db_strat)
    logging.info(f"[STRATEGY] New strategy successfully created: {db_strat.name} (ID: {db_strat.id})")
    return db_strat


@router.get("/all", response_model=List[StrategyOut])
def get_user_strategies(
    current_user: User = Depends(get_current_user), 
    db: Session = Depends(get_db)
):
    """
    Retrieves all strategy presets configured by the authenticated user.
    """
    return db.query(Strategy).filter(Strategy.owner_id == current_user.id).all()


@router.put("/edit/{strategy_id}", response_model=StrategyOut)
def edit_strategy(
    strategy_id: int,
    strat_edit: StrategyEdit,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Updates the parameters of an existing strategy preset.
    """
    db_strat = db.query(Strategy).filter(Strategy.id == strategy_id, Strategy.owner_id == current_user.id).first()
    if not db_strat:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Strategy not found or unauthorized access."
        )

    # Apply only provided fields (StrategyEdit)
    update_data = strat_edit.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(db_strat, key, value)
        
    db.commit()
    db.refresh(db_strat)
    logging.info(f"[STRATEGY] Updated parameters for strategy ID: {strategy_id}")
    return db_strat


@router.delete("/delete/{strategy_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_strategy(
    strategy_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Permanently deletes a strategy preset from the database.
    """
    db_strat = db.query(Strategy).filter(Strategy.id == strategy_id, Strategy.owner_id == current_user.id).first()
    if not db_strat:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Strategy not found or unauthorized access."
        )
        
    db.delete(db_strat)
    db.commit()
    logging.info(f"[STRATEGY] Successfully deleted strategy ID: {strategy_id}")
    return None


@router.post("/toggle/{strategy_id}", response_model=StrategyOut)
def toggle_strategy_state(
    strategy_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Toggles a strategy between ACTIVE (Running) and INACTIVE (Paused).
    When toggled from Inactive -> Active, it automatically resets all live execution states 
    (references, dynamic quantities, and transaction states) to standard baseline values.
    """
    db_strat = db.query(Strategy).filter(Strategy.id == strategy_id, Strategy.owner_id == current_user.id).first()
    if not db_strat:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Strategy not found or unauthorized access."
        )

    # Flip the active state
    db_strat.is_active = not db_strat.is_active
    
    if db_strat.is_active:
        # Reset execution states to start fresh!
        db_strat.ce_reference_close = None
        db_strat.ce_current_qty_lots = db_strat.base_qty_lots
        db_strat.ce_state = "IDLE"
        
        db_strat.pe_reference_close = None
        db_strat.pe_current_qty_lots = db_strat.base_qty_lots
        db_strat.pe_state = "IDLE"
        
        logging.info(f"[STRATEGY ACTIVE] Strategy '{db_strat.name}' (ID: {strategy_id}) has been activated.")
    else:
        logging.info(f"[STRATEGY PAUSED] Strategy '{db_strat.name}' (ID: {strategy_id}) has been paused.")

    db.commit()
    db.refresh(db_strat)
    return db_strat
