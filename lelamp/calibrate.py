#!/usr/bin/env python

import argparse
import logging
from .follower import LeLampFollower, LeLampFollowerConfig

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def calibrate_arm(lamp_id: str, port: str) -> None:
    """Calibrate the robot arm (applies to both leader and follower)."""
    logger.info(f"Starting arm calibration for lamp ID: {lamp_id} on port: {port}")
    
    config = LeLampFollowerConfig(
        port=port,
        id=lamp_id,
    )
    
    arm = LeLampFollower(config)
    
    try:
        # Connect and calibrate
        arm.connect(calibrate=False)
        arm.calibrate()
        logger.info("Arm calibration completed successfully")
    except Exception as e:
        logger.error(f"Arm calibration failed: {e}")
        raise
    finally:
        if arm.is_connected: 
            arm.disconnect()


def main():
    parser = argparse.ArgumentParser(description="Calibrate LeLamp robot arm")
    parser.add_argument('--id', type=str, required=True, help='ID of the lamp')
    parser.add_argument('--port', type=str, required=True, help='Serial port for the lamp')
    args = parser.parse_args()
    
    try:
        print("\n" + "="*50)
        print("ARM CALIBRATION")
        print("="*50)
        calibrate_arm(args.id, args.port)
        
        print("\n" + "="*50)
        print("CALIBRATION COMPLETED SUCCESSFULLY")
        print("="*50)
        
    except Exception as e:
        logger.error(f"Calibration process failed: {e}")
        return 1
    
    return 0


if __name__ == "__main__":
    exit(main())