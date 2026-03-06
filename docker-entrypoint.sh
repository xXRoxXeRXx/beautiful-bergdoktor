#!/bin/bash

# Default run mode
RUN_MODE=${RUN_MODE:-"cron"}

if [ "$RUN_MODE" = "manual" ]; then
    echo "Running in manual mode - executing script once..."
    exec /usr/local/bin/python notifyDoctolibDoctorsAppointment.py
else
    echo "Running in scheduled mode - will execute every 5 minutes..."
    
    # Default schedule interval in minutes
    INTERVAL_MINUTES=${INTERVAL_MINUTES:-5}
    echo "Schedule: Every $INTERVAL_MINUTES minutes"
    
    cd /app
    
    # Main execution loop
    while true; do
        echo "[$(date)] Starting BergdoktorBot execution..."
        
        # Use exec-style run so signals propagate correctly
        /usr/local/bin/python notifyDoctolibDoctorsAppointment.py
        
        echo "[$(date)] Execution completed. Sleeping for $INTERVAL_MINUTES minutes..."
        
        # Sleep for the specified interval (convert minutes to seconds)
        sleep $((INTERVAL_MINUTES * 60))
    done
fi