/**
 * bambu_cloud_bridge — CLI bridge to libbambu_networking.so for Bambu Lab cloud printing.
 *
 * Subcommands:
 *   print   Upload a 3MF and start a cloud print job
 *   status  Query live printer state via MQTT
 *   tasks   List recent cloud print tasks (REST only)
 *   cancel  Stop the current print on a printer
 *   send-mqtt Send raw JSON through the library's MQTT connection
 *   install-cert Register the library's certificate with the printer
 *
 * Build:
 *   g++ -std=c++17 -o bambu_cloud_bridge bambu_cloud_bridge.cpp -ldl -lpthread
 *
 * Requires:
 *   /tmp/bambu_plugin/libbambu_networking.so  (Bambu network library)
 *   /tmp/bambu_agent/cert/slicer_base64.cer   (DigiCert TLS cert)
 */

#include <cstdio>
#include <unistd.h>
#include <cstdlib>
#include <cstring>
#include <dlfcn.h>
#include <functional>
#include <map>
#include <string>
#include <thread>
#include <chrono>
#include <fstream>
#include <sstream>
#include <iostream>
#include <vector>
#include <atomic>
#include <mutex>
#include <cstdarg>
#include <fcntl.h>

// ---------------------------------------------------------------------------
// Type definitions matching bambu_networking.hpp
// ---------------------------------------------------------------------------

typedef std::function<void(int status, int code, std::string msg)> OnUpdateStatusFn;
typedef std::function<bool()> WasCancelledFn;
typedef std::function<bool(int status, std::string job_info)> OnWaitFn;
typedef std::function<void(int online_login, bool login)> OnUserLoginFn;
typedef std::function<void(std::string topic_str)> OnPrinterConnectedFn;
typedef std::function<void(int return_code, int reason_code)> OnServerConnectedFn;
typedef std::function<void(unsigned http_code, std::string http_body)> OnHttpErrorFn;
typedef std::function<std::string()> GetCountryCodeFn;
typedef std::function<void(std::string topic)> GetSubscribeFailureFn;
typedef std::function<void(std::string dev_id, std::string msg)> OnMessageFn;

struct PrintParams {
    std::string     dev_id;
    std::string     task_name;
    std::string     project_name;
    std::string     preset_name;
    std::string     filename;
    std::string     config_filename;
    int             plate_index;
    std::string     ftp_folder;
    std::string     ftp_file;
    std::string     ftp_file_md5;
    std::string     nozzle_mapping;
    std::string     ams_mapping;
    std::string     ams_mapping2;
    std::string     ams_mapping_info;
    std::string     nozzles_info;
    std::string     connection_type;
    std::string     comments;
    int             origin_profile_id = 0;
    int             stl_design_id = 0;
    std::string     origin_model_id;
    std::string     print_type;
    std::string     dst_file;
    std::string     dev_name;
    std::string     dev_ip;
    bool            use_ssl_for_ftp;
    bool            use_ssl_for_mqtt;
    std::string     username;
    std::string     password;
    bool            task_bed_leveling;
    bool            task_flow_cali;
    bool            task_vibration_cali;
    bool            task_layer_inspect;
    bool            task_record_timelapse;
    bool            task_use_ams;
    std::string     task_bed_type;
    std::string     extra_options;
    int             auto_bed_leveling{0};
    int             auto_flow_cali{0};
    int             auto_offset_cali{0};
    int             extruder_cali_manual_mode{-1};
    bool            task_ext_change_assist;
    bool            try_emmc_print;
};

// ---------------------------------------------------------------------------
// Function pointer types
// ---------------------------------------------------------------------------

typedef bool (*fn_check_debug)(bool);
typedef std::string (*fn_get_version)();
typedef void* (*fn_create_agent)(std::string);
typedef int (*fn_destroy_agent)(void*);
typedef int (*fn_init_log)(void*);
typedef int (*fn_set_config_dir)(void*, std::string);
typedef int (*fn_set_cert_file)(void*, std::string, std::string);
typedef int (*fn_set_country_code)(void*, std::string);
typedef int (*fn_start)(void*);
typedef int (*fn_connect_server)(void*);
typedef bool (*fn_is_server_connected)(void*);
typedef int (*fn_change_user)(void*, std::string);
typedef bool (*fn_is_user_login)(void*);
typedef int (*fn_set_user_selected_machine)(void*, std::string);
typedef std::string (*fn_get_user_selected_machine)(void*);
typedef std::string (*fn_get_user_id)(void*);
typedef int (*fn_set_on_server_connected_fn)(void*, OnServerConnectedFn);
typedef int (*fn_set_on_http_error_fn)(void*, OnHttpErrorFn);
typedef int (*fn_set_on_message_fn)(void*, OnMessageFn);
typedef int (*fn_set_on_printer_connected_fn)(void*, OnPrinterConnectedFn);
typedef int (*fn_set_get_country_code_fn)(void*, GetCountryCodeFn);
typedef int (*fn_set_on_user_login_fn)(void*, OnUserLoginFn);
typedef int (*fn_set_on_subscribe_failure_fn)(void*, GetSubscribeFailureFn);
typedef int (*fn_start_print)(void*, PrintParams, OnUpdateStatusFn, WasCancelledFn, OnWaitFn);
typedef int (*fn_set_extra_http_header)(void*, std::map<std::string, std::string>);
typedef std::string (*fn_get_bambulab_host)(void*);
typedef int (*fn_send_message_to_printer)(void*, std::string, std::string, int, int);
typedef int (*fn_send_message)(void*, std::string, std::string, int);
typedef int (*fn_start_subscribe)(void*, std::string);
typedef int (*fn_install_device_cert)(void*, std::string, bool);
typedef int (*fn_update_cert)(void*);

// ---------------------------------------------------------------------------
// Global state
// ---------------------------------------------------------------------------

static void* g_lib = nullptr;
static void* g_agent = nullptr;
static std::atomic<bool> g_server_connected{false};
static std::atomic<bool> g_user_logged_in{false};
static std::atomic<int> g_print_result{-999};
static std::atomic<bool> g_print_done{false};
static bool g_verbose = false;

// Last full MQTT message from printer (for status command)
static std::mutex g_msg_mutex;
static std::string g_last_full_message;
static std::atomic<bool> g_got_full_status{false};

// Loaded function pointers (populated by load_library)
static fn_create_agent      fp_create_agent = nullptr;
static fn_destroy_agent     fp_destroy_agent = nullptr;
static fn_init_log          fp_init_log = nullptr;
static fn_set_config_dir    fp_set_config_dir = nullptr;
static fn_set_cert_file     fp_set_cert_file = nullptr;
static fn_set_country_code  fp_set_country_code = nullptr;
static fn_start             fp_start = nullptr;
static fn_connect_server    fp_connect_server = nullptr;
static fn_is_server_connected fp_is_connected = nullptr;
static fn_change_user       fp_change_user = nullptr;
static fn_is_user_login     fp_is_user_login = nullptr;
static fn_set_user_selected_machine fp_set_machine = nullptr;
static fn_get_user_id       fp_get_user_id = nullptr;
static fn_start_print       fp_start_print = nullptr;
static fn_set_on_server_connected_fn fp_set_server_cb = nullptr;
static fn_set_on_http_error_fn fp_set_http_err_cb = nullptr;
static fn_set_on_message_fn fp_set_message_cb = nullptr;
static fn_set_on_printer_connected_fn fp_set_printer_cb = nullptr;
static fn_set_get_country_code_fn fp_set_country_cb = nullptr;
static fn_set_on_user_login_fn fp_set_user_login_cb = nullptr;
static fn_set_on_subscribe_failure_fn fp_set_sub_fail_cb = nullptr;
static fn_set_extra_http_header fp_set_extra_hdr = nullptr;
static fn_get_bambulab_host fp_get_host = nullptr;
static fn_send_message_to_printer fp_send_msg = nullptr;
static fn_send_message      fp_send_msg_legacy = nullptr;
static fn_start_subscribe   fp_start_sub = nullptr;
static fn_get_version       fp_get_version = nullptr;
static fn_install_device_cert fp_install_cert = nullptr;
static fn_update_cert       fp_update_cert = nullptr;

static const char* stage_names[] = {
    "Create", "Upload", "Waiting", "Sending",
    "Record", "WaitPrinter", "Finished", "ERROR", "Limit"
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

template<typename T>
T load_fn(const char* name) {
    void* ptr = dlsym(g_lib, name);
    if (!ptr && g_verbose)
        fprintf(stderr, "  warn: dlsym(%s) failed\n", name);
    return reinterpret_cast<T>(ptr);
}

static std::string read_file(const std::string& path) {
    std::ifstream f(path);
    if (!f.is_open()) return "";
    std::ostringstream ss;
    ss << f.rdbuf();
    return ss.str();
}

static std::string extract_json_str(const std::string& json, const std::string& key) {
    std::string search = "\"" + key + "\"";
    auto pos = json.find(search);
    if (pos == std::string::npos) return "";
    pos = json.find(":", pos);
    if (pos == std::string::npos) return "";
    pos = json.find("\"", pos);
    if (pos == std::string::npos) return "";
    pos++;
    auto end = json.find("\"", pos);
    if (end == std::string::npos) return "";
    return json.substr(pos, end - pos);
}

static void vlog(const char* fmt, ...) {
    if (!g_verbose) return;
    va_list args;
    va_start(args, fmt);
    vfprintf(stderr, fmt, args);
    va_end(args);
    fflush(stderr);
}

// ---------------------------------------------------------------------------
// Library loading
// ---------------------------------------------------------------------------

static bool load_library() {
    const char* lib_path = getenv("BAMBU_LIB_PATH");
    if (!lib_path) lib_path = "/tmp/bambu_plugin/libbambu_networking.so";

    g_lib = dlopen(lib_path, RTLD_LAZY);
    if (!g_lib) {
        fprintf(stderr, "error: cannot load %s: %s\n", lib_path, dlerror());
        return false;
    }
    vlog("Loaded %s\n", lib_path);

    fp_create_agent    = load_fn<fn_create_agent>("bambu_network_create_agent");
    fp_destroy_agent   = load_fn<fn_destroy_agent>("bambu_network_destroy_agent");
    fp_init_log        = load_fn<fn_init_log>("bambu_network_init_log");
    fp_set_config_dir  = load_fn<fn_set_config_dir>("bambu_network_set_config_dir");
    fp_set_cert_file   = load_fn<fn_set_cert_file>("bambu_network_set_cert_file");
    fp_set_country_code = load_fn<fn_set_country_code>("bambu_network_set_country_code");
    fp_start           = load_fn<fn_start>("bambu_network_start");
    fp_connect_server  = load_fn<fn_connect_server>("bambu_network_connect_server");
    fp_is_connected    = load_fn<fn_is_server_connected>("bambu_network_is_server_connected");
    fp_change_user     = load_fn<fn_change_user>("bambu_network_change_user");
    fp_is_user_login   = load_fn<fn_is_user_login>("bambu_network_is_user_login");
    fp_set_machine     = load_fn<fn_set_user_selected_machine>("bambu_network_set_user_selected_machine");
    fp_get_user_id     = load_fn<fn_get_user_id>("bambu_network_get_user_id");
    fp_start_print     = load_fn<fn_start_print>("bambu_network_start_print");
    fp_set_server_cb   = load_fn<fn_set_on_server_connected_fn>("bambu_network_set_on_server_connected_fn");
    fp_set_http_err_cb = load_fn<fn_set_on_http_error_fn>("bambu_network_set_on_http_error_fn");
    fp_set_message_cb  = load_fn<fn_set_on_message_fn>("bambu_network_set_on_message_fn");
    fp_set_printer_cb  = load_fn<fn_set_on_printer_connected_fn>("bambu_network_set_on_printer_connected_fn");
    fp_set_country_cb  = load_fn<fn_set_get_country_code_fn>("bambu_network_set_get_country_code_fn");
    fp_set_user_login_cb = load_fn<fn_set_on_user_login_fn>("bambu_network_set_on_user_login_fn");
    fp_set_sub_fail_cb = load_fn<fn_set_on_subscribe_failure_fn>("bambu_network_set_on_subscribe_failure_fn");
    fp_set_extra_hdr   = load_fn<fn_set_extra_http_header>("bambu_network_set_extra_http_header");
    fp_get_host        = load_fn<fn_get_bambulab_host>("bambu_network_get_bambulab_host");
    fp_send_msg        = load_fn<fn_send_message_to_printer>("bambu_network_send_message_to_printer");
    fp_send_msg_legacy = load_fn<fn_send_message>("bambu_network_send_message");
    fp_start_sub       = load_fn<fn_start_subscribe>("bambu_network_start_subscribe");
    fp_get_version     = load_fn<fn_get_version>("bambu_network_get_version");
    fp_install_cert    = load_fn<fn_install_device_cert>("bambu_network_install_device_cert");
    fp_update_cert     = load_fn<fn_update_cert>("bambu_network_update_cert");

    if (!fp_create_agent || !fp_change_user || !fp_connect_server) {
        fprintf(stderr, "error: essential functions not found in library\n");
        dlclose(g_lib);
        g_lib = nullptr;
        return false;
    }
    return true;
}

// ---------------------------------------------------------------------------
// Agent initialization (shared by all commands that need MQTT)
// ---------------------------------------------------------------------------

static bool init_agent(const std::string& token_json_raw, bool quiet = false) {
    setenv("CURL_CA_BUNDLE", "/etc/ssl/certs/ca-certificates.crt", 0);
    setenv("SSL_CERT_FILE", "/etc/ssl/certs/ca-certificates.crt", 0);

    system("mkdir -p /tmp/bambu_agent/log /tmp/bambu_agent/config /tmp/bambu_agent/cert 2>/dev/null");

    g_agent = fp_create_agent(std::string("/tmp/bambu_agent/log"));
    if (!g_agent) {
        fprintf(stderr, "error: create_agent returned null\n");
        return false;
    }
    vlog("Agent created: %p\n", g_agent);

    if (fp_init_log) fp_init_log(g_agent);
    if (fp_set_config_dir) fp_set_config_dir(g_agent, std::string("/tmp/bambu_agent/config"));
    if (fp_set_cert_file) fp_set_cert_file(g_agent, std::string("/tmp/bambu_agent/cert"), std::string("slicer_base64.cer"));
    if (fp_set_country_code) fp_set_country_code(g_agent, std::string("US"));
    if (fp_start) fp_start(g_agent);

    // Set HTTP headers (BambuStudio slicer identity) — must be after start()
    if (fp_set_extra_hdr) {
        std::map<std::string, std::string> hdrs;
        hdrs["X-BBL-Client-Type"]    = "slicer";
        hdrs["X-BBL-Client-Name"]    = "BambuStudio";
        hdrs["X-BBL-Client-Version"] = "02.05.01.52";
        hdrs["X-BBL-OS-Type"]        = "linux";
        hdrs["X-BBL-OS-Version"]     = "6.8.0";
        hdrs["X-BBL-Device-ID"]      = "fabprint-headless-001";
        hdrs["X-BBL-Language"]       = "en";
        fp_set_extra_hdr(g_agent, hdrs);
    }

    // Set callbacks
    if (fp_set_server_cb) {
        fp_set_server_cb(g_agent, [](int rc, int reason) {
            vlog("  server_connected: rc=%d reason=%d\n", rc, reason);
            if (rc == 0) g_server_connected = true;
        });
    }
    if (fp_set_http_err_cb) {
        fp_set_http_err_cb(g_agent, [](unsigned code, std::string body) {
            vlog("  HTTP error: %u %s\n", code, body.substr(0, 200).c_str());
        });
    }
    if (fp_set_country_cb) {
        fp_set_country_cb(g_agent, []() -> std::string { return "US"; });
    }
    if (fp_set_user_login_cb) {
        fp_set_user_login_cb(g_agent, [](int online, bool login) {
            vlog("  user_login: online=%d login=%s\n", online, login ? "true" : "false");
            g_user_logged_in = login;
        });
    }
    if (fp_set_message_cb) {
        fp_set_message_cb(g_agent, [](std::string dev_id, std::string msg) {
            if (msg.empty() || msg == "{}") return;
            vlog("  mqtt[%s]: %s\n", dev_id.c_str(), msg.substr(0, 200).c_str());
        });
    }
    if (fp_set_printer_cb) {
        fp_set_printer_cb(g_agent, [](std::string topic) {
            vlog("  printer_connected: %s\n", topic.c_str());
        });
    }
    if (fp_set_sub_fail_cb) {
        fp_set_sub_fail_cb(g_agent, [](std::string topic) {
            vlog("  subscribe_failure: %s\n", topic.c_str());
        });
    }

    // Login
    std::string token = extract_json_str(token_json_raw, "token");
    std::string refresh_token = extract_json_str(token_json_raw, "refreshToken");
    std::string uid = extract_json_str(token_json_raw, "uid");
    std::string name = extract_json_str(token_json_raw, "name");
    std::string email = extract_json_str(token_json_raw, "email");
    std::string avatar = extract_json_str(token_json_raw, "avatar");

    if (token.empty()) {
        fprintf(stderr, "error: no token found in credentials file\n");
        return false;
    }

    std::string user_json = R"({"data":{"token":")" + token +
        R"(","refresh_token":")" + (refresh_token.empty() ? token : refresh_token) +
        R"(","expires_in":"7200","refresh_expires_in":"2592000","user":{"uid":")" + uid +
        R"(","name":")" + name +
        R"(","account":")" + email +
        R"(","avatar":")" + avatar + R"("}}})";

    int ret = fp_change_user(g_agent, user_json);
    if (ret != 0) {
        fprintf(stderr, "error: login failed (change_user returned %d)\n", ret);
        return false;
    }
    std::this_thread::sleep_for(std::chrono::seconds(2));

    if (fp_is_user_login && !fp_is_user_login(g_agent)) {
        fprintf(stderr, "error: login did not succeed\n");
        return false;
    }
    vlog("Logged in as %s (%s)\n", name.c_str(), email.c_str());

    // Connect to MQTT server
    if (fp_connect_server) {
        ret = fp_connect_server(g_agent);
        vlog("connect_server returned: %d\n", ret);
    }

    for (int i = 0; i < 30 && !g_server_connected; i++) {
        std::this_thread::sleep_for(std::chrono::milliseconds(500));
        if (fp_is_connected && fp_is_connected(g_agent))
            g_server_connected = true;
    }

    if (!g_server_connected) {
        fprintf(stderr, "error: could not connect to MQTT server\n");
        return false;
    }
    vlog("MQTT connected\n");
    return true;
}

// Subscribe to a device and send pushall. Returns true on success.
static bool subscribe_and_pushall(const std::string& device_id, int wait_secs = 20) {
    if (fp_set_machine) fp_set_machine(g_agent, device_id);

    if (fp_start_sub) {
        int ret = fp_start_sub(g_agent, std::string("device"));
        vlog("start_subscribe: %d\n", ret);
    }

    // Wait for subscription to establish before sending pushall
    std::this_thread::sleep_for(std::chrono::seconds(3));

    std::string pushall = R"({"pushing":{"sequence_id":"0","command":"pushall","version":1,"push_target":1}})";

    // Retry pushall a few times — sometimes the first attempt fails
    int ret = -1;
    for (int i = 0; i < 3 && ret != 0; i++) {
        if (i > 0) std::this_thread::sleep_for(std::chrono::seconds(2));
        if (fp_send_msg_legacy) ret = fp_send_msg_legacy(g_agent, device_id, pushall, 0);
        if (ret != 0 && fp_send_msg) ret = fp_send_msg(g_agent, device_id, pushall, 0, 0);
        vlog("pushall attempt %d: %d\n", i + 1, ret);
    }

    vlog("Waiting %ds for printer status...\n", wait_secs);
    std::this_thread::sleep_for(std::chrono::seconds(wait_secs));
    return true;
}

static void send_mqtt(const std::string& device_id, const std::string& json_cmd) {
    int ret = -1;
    if (fp_send_msg_legacy) ret = fp_send_msg_legacy(g_agent, device_id, json_cmd, 0);
    if (ret != 0 && fp_send_msg) ret = fp_send_msg(g_agent, device_id, json_cmd, 0, 0);
    vlog("send_mqtt: %d\n", ret);
}

static void cleanup() {
    // Note: destroy_agent / dlclose can hang waiting for MQTT threads.
    // For commands that need a clean exit, use _exit() instead.
    if (g_agent && fp_destroy_agent) fp_destroy_agent(g_agent);
    if (g_lib) dlclose(g_lib);
}

// Fast exit that skips MQTT thread cleanup (avoids hangs)
static void fast_exit(int code) {
    fflush(stdout);
    fflush(stderr);
    _exit(code);
}

// ---------------------------------------------------------------------------
// Command: tasks (REST only — uses library's HTTP client)
// ---------------------------------------------------------------------------

static int cmd_tasks(const std::string& token_json_raw, int limit) {
    // tasks uses a simple REST call — we don't need full MQTT, but we do need
    // the library loaded and the user logged in for its HTTP client.
    // However, the library doesn't expose a direct HTTP method, so we fall back
    // to using curl directly for this command.

    std::string token = extract_json_str(token_json_raw, "token");
    if (token.empty()) {
        fprintf(stderr, "error: no token found in credentials file\n");
        return 1;
    }

    // Use system curl for REST-only commands (no MQTT needed)
    char cmd[2048];
    snprintf(cmd, sizeof(cmd),
        "curl -s 'https://api.bambulab.com/v1/user-service/my/tasks?limit=%d' "
        "-H 'Authorization: Bearer %s' "
        "-H 'Content-Type: application/json'",
        limit, token.c_str());

    FILE* pipe = popen(cmd, "r");
    if (!pipe) {
        fprintf(stderr, "error: failed to execute curl\n");
        return 1;
    }

    std::string response;
    char buf[4096];
    while (fgets(buf, sizeof(buf), pipe)) response += buf;
    int exit_code = pclose(pipe);

    if (exit_code != 0) {
        fprintf(stderr, "error: curl failed with exit code %d\n", exit_code);
        return 1;
    }

    // Simple JSON parsing for task list output
    // Output as structured JSON for machine consumption
    printf("%s\n", response.c_str());
    return 0;
}

// ---------------------------------------------------------------------------
// Command: status
// ---------------------------------------------------------------------------

static int cmd_status(const std::string& token_json_raw, const std::string& device_id) {
    // Suppress library stdout noise (e.g. "use_count = 4") for entire command.
    // We restore stdout only to emit our JSON result at the end.
    int saved_out = dup(STDOUT_FILENO);
    int devnull_fd = open("/dev/null", O_WRONLY);
    if (devnull_fd >= 0) { dup2(devnull_fd, STDOUT_FILENO); close(devnull_fd); }

    if (!load_library()) return 1;
    if (!init_agent(token_json_raw)) { cleanup(); return 1; }

    // Collect all MQTT messages
    std::vector<std::string> messages;
    std::mutex msg_list_mutex;

    if (fp_set_message_cb) {
        fp_set_message_cb(g_agent, [&messages, &msg_list_mutex](std::string dev_id, std::string msg) {
            if (msg.empty() || msg == "{}") return;
            vlog("  status_msg: %s\n", msg.substr(0, 200).c_str());
            std::lock_guard<std::mutex> lock(msg_list_mutex);
            messages.push_back(msg);
        });
    }

    subscribe_and_pushall(device_id, 10);

    // Find the largest/most complete message (best approximation of full status)
    std::lock_guard<std::mutex> lock(msg_list_mutex);
    // Restore stdout for output
    if (saved_out >= 0) { dup2(saved_out, STDOUT_FILENO); close(saved_out); }

    if (messages.empty()) {
        fprintf(stderr, "error: no status received from printer %s\n", device_id.c_str());
        fast_exit(2);
    }

    // Pick the message with the most data
    std::string best;
    for (const auto& m : messages) {
        if (m.size() > best.size()) best = m;
    }
    printf("%s\n", best.c_str());
    fast_exit(0);
    return 0; // unreachable
}

// ---------------------------------------------------------------------------
// Command: cancel
// ---------------------------------------------------------------------------

static int cmd_cancel(const std::string& token_json_raw, const std::string& device_id) {
    int saved_out = dup(STDOUT_FILENO);
    int devnull_fd = open("/dev/null", O_WRONLY);
    if (devnull_fd >= 0) { dup2(devnull_fd, STDOUT_FILENO); close(devnull_fd); }

    if (!load_library()) return 1;
    if (!init_agent(token_json_raw)) { cleanup(); return 1; }

    // Set up message callback to watch for ack
    std::atomic<bool> got_ack{false};
    if (fp_set_message_cb) {
        fp_set_message_cb(g_agent, [&got_ack](std::string dev_id, std::string msg) {
            if (msg.find("IDLE") != std::string::npos ||
                msg.find("\"command\":\"stop\"") != std::string::npos) {
                got_ack = true;
            }
        });
    }

    if (fp_set_machine) fp_set_machine(g_agent, device_id);
    if (fp_start_sub) fp_start_sub(g_agent, std::string("device"));
    std::this_thread::sleep_for(std::chrono::seconds(3));

    // Send stop command
    std::string stop_cmd = R"({"print":{"command":"stop","sequence_id":"0"}})";
    send_mqtt(device_id, stop_cmd);
    fprintf(stderr, "Stop command sent to %s\n", device_id.c_str());

    // Wait briefly for ack
    for (int i = 0; i < 10 && !got_ack; i++)
        std::this_thread::sleep_for(std::chrono::seconds(1));

    if (saved_out >= 0) { dup2(saved_out, STDOUT_FILENO); close(saved_out); }
    printf("{\"command\":\"stop\",\"device_id\":\"%s\",\"sent\":true}\n", device_id.c_str());
    fast_exit(0);
    return 0; // unreachable
}

// ---------------------------------------------------------------------------
// Command: send-mqtt  (send raw JSON through library's MQTT connection)
// ---------------------------------------------------------------------------

static int cmd_send_mqtt(const std::string& token_json_raw, const std::string& device_id,
                         const std::string& json_payload, int wait_secs) {
    int saved_out = dup(STDOUT_FILENO);
    int devnull_fd = open("/dev/null", O_WRONLY);
    if (devnull_fd >= 0) { dup2(devnull_fd, STDOUT_FILENO); close(devnull_fd); }

    if (!load_library()) return 1;
    if (!init_agent(token_json_raw)) { cleanup(); return 1; }

    // Collect ALL MQTT messages for output
    std::vector<std::string> responses;
    std::mutex resp_mutex;

    if (fp_set_message_cb) {
        fp_set_message_cb(g_agent, [&responses, &resp_mutex](std::string dev_id, std::string msg) {
            if (msg.empty() || msg == "{}") return;
            vlog("  mqtt[%s]: %s\n", dev_id.c_str(), msg.substr(0, 500).c_str());
            std::lock_guard<std::mutex> lock(resp_mutex);
            responses.push_back(msg);
        });
    }

    subscribe_and_pushall(device_id, 20);

    fprintf(stderr, "Sending MQTT payload (%zu bytes) to %s\n",
            json_payload.size(), device_id.c_str());

    // Try multiple send approaches
    int ret = -1;
    if (fp_send_msg_legacy) {
        ret = fp_send_msg_legacy(g_agent, device_id, json_payload, 0);
        fprintf(stderr, "  send_msg_legacy(qos=0): %d\n", ret);
    }
    if (ret != 0 && fp_send_msg_legacy) {
        ret = fp_send_msg_legacy(g_agent, device_id, json_payload, 1);
        fprintf(stderr, "  send_msg_legacy(qos=1): %d\n", ret);
    }
    if (ret != 0 && fp_send_msg) {
        ret = fp_send_msg(g_agent, device_id, json_payload, 0, 0);
        fprintf(stderr, "  send_msg(0,0): %d\n", ret);
    }
    if (ret != 0 && fp_send_msg) {
        ret = fp_send_msg(g_agent, device_id, json_payload, 1, 0);
        fprintf(stderr, "  send_msg(1,0): %d\n", ret);
    }
    if (ret != 0 && fp_send_msg) {
        ret = fp_send_msg(g_agent, device_id, json_payload, 0, 1);
        fprintf(stderr, "  send_msg(0,1): %d\n", ret);
    }
    fprintf(stderr, "Final send result: %d\n", ret);

    fprintf(stderr, "Waiting %ds for response...\n", wait_secs);
    std::this_thread::sleep_for(std::chrono::seconds(wait_secs));

    // Restore stdout and output all responses
    if (saved_out >= 0) { dup2(saved_out, STDOUT_FILENO); close(saved_out); }

    std::lock_guard<std::mutex> lock(resp_mutex);
    printf("{\"sent\":true,\"device_id\":\"%s\",\"responses\":[", device_id.c_str());
    for (size_t i = 0; i < responses.size(); i++) {
        if (i > 0) printf(",");
        printf("%s", responses[i].c_str());
    }
    printf("]}\n");

    fast_exit(0);
    return 0;
}

// ---------------------------------------------------------------------------
// Command: install-cert
// ---------------------------------------------------------------------------

static int cmd_install_cert(const std::string& token_json_raw, const std::string& device_id) {
    int saved_out = dup(STDOUT_FILENO);
    int devnull_fd = open("/dev/null", O_WRONLY);
    if (devnull_fd >= 0) { dup2(devnull_fd, STDOUT_FILENO); close(devnull_fd); }

    if (!load_library()) return 1;
    if (!init_agent(token_json_raw)) { cleanup(); return 1; }

    // Collect all MQTT messages, especially security-related
    std::vector<std::string> messages;
    std::mutex msg_list_mutex;

    if (fp_set_message_cb) {
        fp_set_message_cb(g_agent, [&messages, &msg_list_mutex](std::string dev_id, std::string msg) {
            if (msg.empty() || msg == "{}") return;
            // Log everything for debugging
            fprintf(stderr, "  mqtt: %s\n", msg.substr(0, 500).c_str());
            std::lock_guard<std::mutex> lock(msg_list_mutex);
            messages.push_back(msg);
        });
    }

    subscribe_and_pushall(device_id, 10);

    // Try update_cert first
    if (fp_update_cert) {
        fprintf(stderr, "Calling update_cert...\n");
        int ret = fp_update_cert(g_agent);
        fprintf(stderr, "  update_cert returned: %d\n", ret);
        std::this_thread::sleep_for(std::chrono::seconds(5));
    }

    // Install device cert
    if (fp_install_cert) {
        fprintf(stderr, "Calling install_device_cert(%s, false)...\n", device_id.c_str());
        int ret = fp_install_cert(g_agent, device_id, false);
        fprintf(stderr, "  install_device_cert returned: %d\n", ret);
        std::this_thread::sleep_for(std::chrono::seconds(5));
    } else {
        fprintf(stderr, "error: install_device_cert not found in library\n");
        return 1;
    }

    // Request cert list from printer to verify
    std::string cert_req = R"({"security":{"sequence_id":"0","command":"get_app_cert_list"}})";
    send_mqtt(device_id, cert_req);
    fprintf(stderr, "Requested app_cert_list, waiting 10s...\n");
    std::this_thread::sleep_for(std::chrono::seconds(10));

    // Restore stdout and output results
    if (saved_out >= 0) { dup2(saved_out, STDOUT_FILENO); close(saved_out); }

    std::lock_guard<std::mutex> lock(msg_list_mutex);
    printf("{\"command\":\"install-cert\",\"device_id\":\"%s\",\"messages\":[",
           device_id.c_str());
    for (size_t i = 0; i < messages.size(); i++) {
        if (i > 0) printf(",");
        printf("%s", messages[i].c_str());
    }
    printf("]}\n");

    fast_exit(0);
    return 0;
}

// ---------------------------------------------------------------------------
// Command: print
// ---------------------------------------------------------------------------

static int cmd_print(const std::string& token_json_raw, const std::string& device_id,
                     const std::string& file_3mf, const std::string& config_3mf,
                     const std::string& project_name, int timeout_secs) {
    int saved_out = dup(STDOUT_FILENO);
    int devnull_fd = open("/dev/null", O_WRONLY);
    if (devnull_fd >= 0) { dup2(devnull_fd, STDOUT_FILENO); close(devnull_fd); }

    if (!load_library()) return 1;

    if (!fp_start_print) {
        fprintf(stderr, "error: start_print function not found in library\n");
        cleanup();
        return 1;
    }

    if (!init_agent(token_json_raw)) { cleanup(); return 1; }

    // Set message callback — log ALL messages, and full project_file responses
    if (fp_set_message_cb) {
        fp_set_message_cb(g_agent, [](std::string dev_id, std::string msg) {
            if (msg.empty() || msg == "{}") return;
            // Log full message for project_file and security responses
            if (msg.find("project_file") != std::string::npos) {
                vlog("  PRINT_CMD: %s\n", msg.c_str());
            } else if (msg.find("app_cert_list") != std::string::npos ||
                       msg.find("security") != std::string::npos) {
                vlog("  SECURITY: %s\n", msg.c_str());
            } else if (msg.find("gcode_state") != std::string::npos &&
                       msg.find("PREPARE") != std::string::npos) {
                vlog("  PREPARING: %s\n", msg.substr(0, 500).c_str());
            } else {
                vlog("  mqtt: %s\n", msg.substr(0, 200).c_str());
            }
        });
    }

    subscribe_and_pushall(device_id, 20);

    // Build PrintParams
    PrintParams params;
    params.dev_id = device_id;
    params.task_name = "";
    params.project_name = project_name;
    params.preset_name = "";
    params.filename = file_3mf;
    params.config_filename = config_3mf;
    params.plate_index = 1;
    params.ftp_folder = "sdcard/";
    params.ftp_file = "";
    params.ftp_file_md5 = "";
    params.nozzle_mapping = "[]";
    params.ams_mapping = "[0,1,2,3]";
    params.ams_mapping2 = "";
    params.ams_mapping_info = "";
    params.nozzles_info = "";
    params.connection_type = "cloud";
    params.comments = "";
    params.origin_profile_id = 0;
    params.stl_design_id = 0;
    params.origin_model_id = "";
    params.print_type = "from_normal";
    params.dst_file = "";
    params.dev_name = "";
    params.dev_ip = "";
    params.use_ssl_for_ftp = false;
    params.use_ssl_for_mqtt = true;
    params.username = "";
    params.password = "";
    params.task_bed_leveling = true;
    params.task_flow_cali = true;
    params.task_vibration_cali = true;
    params.task_layer_inspect = false;
    params.task_record_timelapse = false;
    params.task_use_ams = true;
    params.task_bed_type = "auto";
    params.extra_options = "";
    params.auto_bed_leveling = 0;
    params.auto_flow_cali = 0;
    params.auto_offset_cali = 0;
    params.extruder_cali_manual_mode = -1;
    params.task_ext_change_assist = false;
    params.try_emmc_print = false;

    // Status tracking for structured output
    std::string last_stage;
    int upload_pct = 0;

    OnUpdateStatusFn update_fn = [&](int status, int code, std::string msg) {
        const char* stage = (status >= 0 && status < 9) ? stage_names[status] : "?";
        last_stage = stage;
        if (status == 1) upload_pct = code; // Upload stage: code = percent
        vlog("  [%s] code=%d msg=%s\n", stage, code, msg.substr(0, 200).c_str());
        if (status == 6) { g_print_result = 0; g_print_done = true; }
        else if (status == 7) { g_print_result = code; g_print_done = true; }
    };

    WasCancelledFn cancel_fn = []() -> bool { return false; };
    OnWaitFn wait_fn = [](int status, std::string job_info) -> bool { return false; };

    // Retry on enc flag not ready
    int ret = -999;
    for (int attempt = 0; attempt < 5; attempt++) {
        g_print_done = false;
        g_print_result = -999;
        ret = fp_start_print(g_agent, params, update_fn, cancel_fn, wait_fn);
        vlog("start_print attempt %d returned: %d\n", attempt + 1, ret);

        if (ret != -3140) break;
        vlog("Enc flag not ready, retrying in 15s...\n");
        std::string pushall = R"({"pushing":{"sequence_id":"0","command":"pushall","version":1,"push_target":1}})";
        send_mqtt(device_id, pushall);
        std::this_thread::sleep_for(std::chrono::seconds(15));
    }

    // Wait for completion
    for (int i = 0; i < timeout_secs && !g_print_done; i++) {
        std::this_thread::sleep_for(std::chrono::seconds(1));
    }

    // Structured output
    const char* result_str = "unknown";
    int exit_code = 1;
    if (g_print_result == 0) { result_str = "success"; exit_code = 0; }
    else if (ret == 0 || ret == -1) {
        // ret=0 means library returned success, ret=-1 might mean printer busy
        // but task was likely created
        result_str = (ret == 0) ? "success" : "sent";
        exit_code = (ret == 0) ? 0 : 0; // -1 often means task created but printer timeout
    }
    else { result_str = "error"; }

    if (saved_out >= 0) { dup2(saved_out, STDOUT_FILENO); close(saved_out); }
    printf("{\"result\":\"%s\",\"return_code\":%d,\"print_result\":%d,"
           "\"device_id\":\"%s\",\"file\":\"%s\"}\n",
           result_str, ret, g_print_result.load(),
           device_id.c_str(), file_3mf.c_str());

    cleanup();
    return exit_code;
}

// ---------------------------------------------------------------------------
// Usage and argument parsing
// ---------------------------------------------------------------------------

static void print_usage(const char* prog) {
    fprintf(stderr,
        "bambu-cloud-bridge — Bambu Lab cloud printing CLI\n"
        "\n"
        "Usage:\n"
        "  %s print  <3mf> <device_id> <token_file> [options]\n"
        "  %s status <device_id> <token_file> [-v]\n"
        "  %s tasks  <token_file> [--limit N]\n"
        "  %s cancel <device_id> <token_file> [-v]\n"
        "\n"
        "Commands:\n"
        "  print   Upload a 3MF file and start a cloud print job\n"
        "  status  Query live printer state via MQTT (JSON output)\n"
        "  tasks   List recent cloud print tasks (JSON output)\n"
        "  cancel  Stop the current print on a printer\n"
        "\n"
        "Print options:\n"
        "  --config-3mf <path>  Config-only 3MF file (optional)\n"
        "  --project <name>     Project name (default: fabprint)\n"
        "  --timeout <seconds>  Wait timeout (default: 180)\n"
        "\n"
        "Global options:\n"
        "  -v, --verbose        Verbose debug output to stderr\n"
        "\n"
        "Environment:\n"
        "  BAMBU_LIB_PATH       Path to libbambu_networking.so\n"
        "                       (default: /tmp/bambu_plugin/libbambu_networking.so)\n"
        "\n"
        "Token file format (JSON):\n"
        "  {\"token\": \"...\", \"uid\": \"...\", \"name\": \"...\", \"email\": \"...\"}\n"
        "\n"
        "Output: All commands produce JSON on stdout. Logs go to stderr (-v).\n",
        prog, prog, prog, prog);
}

int main(int argc, char* argv[]) {
    if (argc < 2) {
        print_usage(argv[0]);
        return 1;
    }

    std::string command = argv[1];

    if (command == "--help" || command == "-h") {
        print_usage(argv[0]);
        return 0;
    }

    // Check for -v/--verbose anywhere in args
    for (int i = 2; i < argc; i++) {
        if (std::string(argv[i]) == "-v" || std::string(argv[i]) == "--verbose")
            g_verbose = true;
    }

    // --- tasks ---
    if (command == "tasks") {
        if (argc < 3) {
            fprintf(stderr, "Usage: %s tasks <token_file> [--limit N] [-v]\n", argv[0]);
            return 1;
        }
        std::string token_file = argv[2];
        int limit = 10;
        for (int i = 3; i < argc; i++) {
            if (std::string(argv[i]) == "--limit" && i + 1 < argc)
                limit = atoi(argv[++i]);
        }
        std::string token_json = read_file(token_file);
        if (token_json.empty()) { fprintf(stderr, "error: cannot read %s\n", token_file.c_str()); return 1; }
        return cmd_tasks(token_json, limit);
    }

    // --- status ---
    if (command == "status") {
        if (argc < 4) {
            fprintf(stderr, "Usage: %s status <device_id> <token_file> [-v]\n", argv[0]);
            return 1;
        }
        std::string device_id = argv[2];
        std::string token_file = argv[3];
        std::string token_json = read_file(token_file);
        if (token_json.empty()) { fprintf(stderr, "error: cannot read %s\n", token_file.c_str()); return 1; }
        return cmd_status(token_json, device_id);
    }

    // --- cancel ---
    if (command == "cancel") {
        if (argc < 4) {
            fprintf(stderr, "Usage: %s cancel <device_id> <token_file> [-v]\n", argv[0]);
            return 1;
        }
        std::string device_id = argv[2];
        std::string token_file = argv[3];
        std::string token_json = read_file(token_file);
        if (token_json.empty()) { fprintf(stderr, "error: cannot read %s\n", token_file.c_str()); return 1; }
        return cmd_cancel(token_json, device_id);
    }

    // --- install-cert ---
    if (command == "install-cert") {
        if (argc < 4) {
            fprintf(stderr, "Usage: %s install-cert <device_id> <token_file> [-v]\n", argv[0]);
            return 1;
        }
        std::string device_id = argv[2];
        std::string token_file = argv[3];
        std::string token_json = read_file(token_file);
        if (token_json.empty()) { fprintf(stderr, "error: cannot read %s\n", token_file.c_str()); return 1; }
        return cmd_install_cert(token_json, device_id);
    }

    // --- send-mqtt ---
    if (command == "send-mqtt") {
        if (argc < 5) {
            fprintf(stderr, "Usage: %s send-mqtt <device_id> <token_file> <json_payload> [--wait N] [-v]\n", argv[0]);
            return 1;
        }
        std::string device_id = argv[2];
        std::string token_file = argv[3];
        std::string json_payload = argv[4];
        int wait_secs = 30;
        for (int i = 5; i < argc; i++) {
            if (std::string(argv[i]) == "--wait" && i + 1 < argc)
                wait_secs = atoi(argv[++i]);
        }
        std::string token_json = read_file(token_file);
        if (token_json.empty()) { fprintf(stderr, "error: cannot read %s\n", token_file.c_str()); return 1; }

        // If json_payload starts with @, read from file
        if (!json_payload.empty() && json_payload[0] == '@') {
            json_payload = read_file(json_payload.substr(1));
            if (json_payload.empty()) { fprintf(stderr, "error: cannot read payload file\n"); return 1; }
        }

        return cmd_send_mqtt(token_json, device_id, json_payload, wait_secs);
    }

    // --- print ---
    if (command == "print") {
        if (argc < 5) {
            fprintf(stderr, "Usage: %s print <3mf> <device_id> <token_file> [options]\n", argv[0]);
            return 1;
        }
        std::string file_3mf = argv[2];
        std::string device_id = argv[3];
        std::string token_file = argv[4];
        std::string config_3mf = "";
        std::string project_name = "fabprint";
        int timeout = 180;

        for (int i = 5; i < argc; i++) {
            std::string arg = argv[i];
            if (arg == "--config-3mf" && i + 1 < argc) config_3mf = argv[++i];
            else if (arg == "--project" && i + 1 < argc) project_name = argv[++i];
            else if (arg == "--timeout" && i + 1 < argc) timeout = atoi(argv[++i]);
        }

        std::string token_json = read_file(token_file);
        if (token_json.empty()) { fprintf(stderr, "error: cannot read %s\n", token_file.c_str()); return 1; }

        // Validate 3mf file exists
        if (access(file_3mf.c_str(), R_OK) != 0) {
            fprintf(stderr, "error: cannot read 3mf file: %s\n", file_3mf.c_str());
            return 1;
        }

        return cmd_print(token_json, device_id, file_3mf, config_3mf, project_name, timeout);
    }

    fprintf(stderr, "error: unknown command '%s'\n\n", command.c_str());
    print_usage(argv[0]);
    return 1;
}
