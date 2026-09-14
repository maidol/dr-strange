const std = @import("std");

// The Zig client binds the C library (sdk/c): it imports the translation of
// drsg.h and compiles the C sources directly, linking system libcurl + json-c
// via pkg-config.
fn wireC(mod: *std.Build.Module, b: *std.Build) void {
    mod.link_libc = true;
    mod.addIncludePath(b.path("../c/include"));
    // drsg.h includes <json-c/json.h>; pkg-config's --cflags points inside the
    // json-c directory, so ask it for the parent instead of guessing a prefix.
    const jsonc_inc = std.mem.trim(
        u8,
        b.run(&.{ "pkg-config", "--variable=includedir", "json-c" }),
        " \r\n",
    );
    mod.addSystemIncludePath(.{ .cwd_relative = jsonc_inc });
    mod.addCSourceFiles(.{
        .root = b.path("../c"),
        .files = &.{ "src/drsg.c", "src/drsg_generated.c" },
        .flags = &.{"-std=gnu11"},
    });
    mod.linkSystemLibrary("json-c", .{ .use_pkg_config = .force });
    mod.linkSystemLibrary("libcurl", .{ .use_pkg_config = .force });
}

// `drsg.h` as a Zig module, for `@import("c")`.
//
// A build step rather than `@cImport`, which Zig removed in 0.17 — the same
// translation either way, named in `src/c.h` instead of inline in the source
// that uses it.
fn translateC(b: *std.Build, target: std.Build.ResolvedTarget, optimize: std.builtin.OptimizeMode) *std.Build.Module {
    const translated = b.addTranslateC(.{
        .root_source_file = b.path("src/c.h"),
        .target = target,
        .optimize = optimize,
    });
    translated.addIncludePath(b.path("../c/include"));
    translated.addSystemIncludePath(.{ .cwd_relative = "/usr/include" });
    return translated.createModule();
}

pub fn build(b: *std.Build) void {
    const target = b.standardTargetOptions(.{});
    const optimize = b.standardOptimizeOption(.{});

    const drsg_mod = b.addModule("drsg", .{
        .root_source_file = b.path("src/drsg.zig"),
        .target = target,
        .optimize = optimize,
    });
    drsg_mod.addImport("c", translateC(b, target, optimize));
    wireC(drsg_mod, b);

    // e2e test (drives a real drsg serve; started by test/run.sh).
    const test_mod = b.createModule(.{
        .root_source_file = b.path("test/e2e.zig"),
        .target = target,
        .optimize = optimize,
    });
    test_mod.addImport("drsg", drsg_mod);

    const tests = b.addTest(.{ .root_module = test_mod });
    const run_tests = b.addRunArtifact(tests);
    const test_step = b.step("test", "Run the e2e test (needs $DRSG_BASE_URL; see test/run.sh)");
    test_step.dependOn(&run_tests.step);

    // Quickstart example: `zig build example` (needs a drsg serve on :7700).
    const example_mod = b.createModule(.{
        .root_source_file = b.path("examples/quickstart.zig"),
        .target = target,
        .optimize = optimize,
    });
    example_mod.addImport("drsg", drsg_mod);
    const example_exe = b.addExecutable(.{ .name = "quickstart", .root_module = example_mod });
    const run_example = b.addRunArtifact(example_exe);
    const example_step = b.step("example", "Run the quickstart example");
    example_step.dependOn(&run_example.step);
}
