# What counts as a test run or a build, shared by bin/hook and bin/record.
# Bash extended regular expressions; sourced, not run.

# Wrappers that may come first: `time`, `/usr/bin/time -p`, `nice`, `timeout 600`, `env`.
wrappers='((/usr/bin/)?time( -p)? |nice( -n -?[0-9]+)? |timeout [0-9]+[smh]? |env )*'
# Launchers in front of the runner itself.
launchers='(npx |pnpm (exec |dlx )?|yarn |bunx |uv run |poetry run |python3? -m |(ba)?sh )?'
# A project script: ./build.sh, scripts/test-all.sh, ci/run_build.sh ...
script_dir='(\./|[A-Za-z0-9_.~/-]*/)?'

tests="(vitest|jest|pytest|cargo test|cargo nextest run|go test|deno test|forge test|(npm|pnpm|yarn|bun) (run )?test(:[A-Za-z0-9_-]+)?|(make|just|task) test|${script_dir}(test|[A-Za-z0-9_-]*[-_]test)[A-Za-z0-9_-]*\\.sh)"
builds="(forge build|cargo build|cargo check|go build|tsc|next build|vite build|(npm|pnpm|yarn|bun) run build(:[A-Za-z0-9_-]+)?|(pnpm|yarn) build|(make|just|task) build|swift build|${script_dir}(build|[A-Za-z0-9_-]*[-_]build)[A-Za-z0-9_-]*\\.sh)"
