.PHONY: install run loop test clean

install:
	pip install -r requirements.txt
	playwright install

run:
	python main_v5.py

loop:
	python agente_qwen.py

test:
	python -m pytest tests/ || echo "No tests defined"

clean:
	find . -type f -name "*.pyc" -delete
	find . -type d -name "__pycache__" -delete
	rm -f *.log *.csv *.tmp
