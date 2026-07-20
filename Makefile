.PHONY: deps lint

deps:
	@for chart in umbrella-charts/*/* bootstrap/helm/*; do \
		if [ -f "$$chart/Chart.yaml" ]; then echo "==> $$chart"; helm dependency update "$$chart"; fi; \
	done

lint:
	@for chart in umbrella-charts/*/*; do \
		if [ -f "$$chart/Chart.yaml" ]; then echo "==> $$chart"; helm lint "$$chart"; fi; \
	done
