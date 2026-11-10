#include <megaapi.h>

#include <algorithm>
#include <chrono>
#include <csignal>
#include <condition_variable>
#include <cstdint>
#include <cstdlib>
#include <cctype>
#include <filesystem>
#include <iostream>
#include <memory>
#include <mutex>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

namespace {

constexpr auto REQUEST_TIMEOUT = std::chrono::minutes(5);
constexpr std::size_t MAX_FILES = 2000;
constexpr std::size_t MAX_DIRECTORIES = 500;
constexpr int MAX_DEPTH = 20;

volatile std::sig_atomic_t interrupt_requested = 0;
std::mutex output_mutex;

void handle_interrupt(int) {
    interrupt_requested = 1;
}

std::string json_escape(const std::string& value) {
    std::string result;
    result.reserve(value.size() + 2);
    for (unsigned char c : value) {
        switch (c) {
            case '"': result += "\\\""; break;
            case '\\': result += "\\\\"; break;
            case '\b': result += "\\b"; break;
            case '\f': result += "\\f"; break;
            case '\n': result += "\\n"; break;
            case '\r': result += "\\r"; break;
            case '\t': result += "\\t"; break;
            default:
                if (c < 0x20) {
                    constexpr char hex[] = "0123456789abcdef";
                    result += "\\u00";
                    result += hex[c >> 4];
                    result += hex[c & 0x0f];
                } else {
                    result += static_cast<char>(c);
                }
        }
    }
    return result;
}

void emit_error(const std::string& message) {
    std::lock_guard<std::mutex> lock(output_mutex);
    std::cout << "{\"event\":\"error\",\"message\":\""
              << json_escape(message) << "\"}\n" << std::flush;
}

bool is_folder_link(const std::string& link) {
    std::string normalized = link;
    std::transform(normalized.begin(), normalized.end(), normalized.begin(), [](unsigned char ch) {
        return static_cast<char>(std::tolower(ch));
    });
    return normalized.find("/folder/") != std::string::npos
        || normalized.find("#f!") != std::string::npos;
}

class SdkListener final
    : public mega::MegaRequestListener,
      public mega::MegaTransferListener {
public:
    void begin_request() {
        std::lock_guard<std::mutex> lock(mutex_);
        request_done_ = false;
        request_error_.clear();
        request_node_.reset();
    }

    bool wait_request(std::string& error) {
        std::unique_lock<std::mutex> lock(mutex_);
        if (!condition_.wait_for(lock, REQUEST_TIMEOUT, [this] { return request_done_; })) {
            error = "MEGA request timed out. Try again later.";
            return false;
        }
        error = request_error_;
        return request_error_.empty();
    }

    std::unique_ptr<mega::MegaNode> take_request_node() {
        std::lock_guard<std::mutex> lock(mutex_);
        return std::move(request_node_);
    }

    void begin_transfer() {
        std::lock_guard<std::mutex> lock(mutex_);
        transfer_done_ = false;
        transfer_error_.clear();
    }

    bool wait_transfer(mega::MegaCancelToken* cancel_token, std::string& error) {
        std::unique_lock<std::mutex> lock(mutex_);
        const auto deadline = std::chrono::steady_clock::now() + std::chrono::hours(24);
        while (!transfer_done_) {
            if (interrupt_requested) {
                cancel_token->cancel();
            }
            if (condition_.wait_for(lock, std::chrono::milliseconds(250)) == std::cv_status::timeout
                && std::chrono::steady_clock::now() >= deadline) {
                cancel_token->cancel();
                error = "MEGA download timed out.";
                return false;
            }
        }
        error = transfer_error_;
        return transfer_error_.empty();
    }

    void onRequestFinish(
        mega::MegaApi*,
        mega::MegaRequest* request,
        mega::MegaError* error
    ) override {
        std::lock_guard<std::mutex> lock(mutex_);
        if (error->getErrorCode() != mega::MegaError::API_OK) {
            request_error_ = error->getErrorString();
        } else if (request->getType() == mega::MegaRequest::TYPE_GET_PUBLIC_NODE) {
            request_node_.reset(request->getPublicMegaNode());
            if (!request_node_) {
                request_error_ = "MEGA did not return a file for this public link.";
            }
        }
        request_done_ = true;
        condition_.notify_all();
    }

    void onTransferUpdate(mega::MegaApi*, mega::MegaTransfer* transfer) override {
        std::lock_guard<std::mutex> lock(output_mutex);
        std::cout << "{\"event\":\"progress\",\"downloaded\":"
                  << transfer->getTransferredBytes() << ",\"total\":";
        const auto total = transfer->getTotalBytes();
        if (total >= 0) {
            std::cout << total;
        } else {
            std::cout << "null";
        }
        std::cout << "}\n" << std::flush;
    }

    void onTransferFinish(
        mega::MegaApi*,
        mega::MegaTransfer*,
        mega::MegaError* error
    ) override {
        std::lock_guard<std::mutex> lock(mutex_);
        if (error->getErrorCode() != mega::MegaError::API_OK) {
            transfer_error_ = error->getErrorString();
        }
        transfer_done_ = true;
        condition_.notify_all();
    }

private:
    std::mutex mutex_;
    std::condition_variable condition_;
    bool request_done_ = false;
    bool transfer_done_ = false;
    std::string request_error_;
    std::string transfer_error_;
    std::unique_ptr<mega::MegaNode> request_node_;
};

std::unique_ptr<mega::MegaApi> create_api() {
    const char* app_key = std::getenv("MEGA_APP_KEY");
    if (!app_key || !*app_key) {
        throw std::runtime_error(
            "Set MEGA_APP_KEY to a valid MEGA developer application key to use MEGA links."
        );
    }
    mega::MegaApi::setLogLevel(mega::MegaApi::LOG_LEVEL_FATAL);
    return std::make_unique<mega::MegaApi>(app_key, "/tmp", "AetherDrive/1.0");
}

std::string require_stdin_link() {
    std::string link;
    if (!std::getline(std::cin, link) || link.empty() || link.size() > 4096) {
        throw std::runtime_error("A valid MEGA public link is required.");
    }
    return link;
}

struct FileEntry {
    std::uint64_t handle;
    std::string path;
    long long size;
};

void collect_files(
    mega::MegaApi* api,
    mega::MegaNode* parent,
    const std::string& parent_path,
    int depth,
    std::vector<FileEntry>& files,
    std::size_t& directory_count
) {
    if (depth > MAX_DEPTH) {
        throw std::runtime_error("The MEGA folder tree is nested too deeply.");
    }
    std::unique_ptr<mega::MegaNodeList> children(api->getChildren(parent));
    if (!children) {
        throw std::runtime_error("The MEGA SDK could not read this folder.");
    }
    for (int index = 0; index < children->size(); ++index) {
        mega::MegaNode* child = children->get(index);
        const char* child_name = child ? child->getName() : nullptr;
        if (!child || !child_name || !*child_name) {
            continue;
        }
        const std::string path = parent_path.empty()
            ? child_name
            : parent_path + "/" + child_name;
        if (child->isFile()) {
            if (files.size() >= MAX_FILES) {
                throw std::runtime_error("The MEGA folder contains more than 2,000 files.");
            }
            files.push_back({child->getHandle(), path, child->getSize()});
        } else {
            if (++directory_count > MAX_DIRECTORIES) {
                throw std::runtime_error("The MEGA folder tree contains too many folders.");
            }
            collect_files(api, child, path, depth + 1, files, directory_count);
        }
    }
}

void inspect_link(const std::string& link) {
    auto api = create_api();
    SdkListener listener;
    std::string error;
    std::string kind;
    std::string name;
    std::vector<FileEntry> files;

    if (is_folder_link(link)) {
        listener.begin_request();
        api->loginToFolder(link.c_str(), &listener);
        if (!listener.wait_request(error)) {
            throw std::runtime_error(error);
        }
        listener.begin_request();
        api->fetchNodes(&listener);
        if (!listener.wait_request(error)) {
            throw std::runtime_error(error);
        }
        std::unique_ptr<mega::MegaNode> root(api->getRootNode());
        const char* root_name = root ? root->getName() : nullptr;
        if (!root || !root_name || !*root_name) {
            throw std::runtime_error("The MEGA folder link has no readable root folder.");
        }
        kind = "folder";
        name = root_name;
        std::size_t directory_count = 0;
        collect_files(api.get(), root.get(), "", 0, files, directory_count);
    } else {
        listener.begin_request();
        api->getPublicNode(link.c_str(), &listener);
        if (!listener.wait_request(error)) {
            throw std::runtime_error(error);
        }
        auto node = listener.take_request_node();
        const char* node_name = node ? node->getName() : nullptr;
        if (!node || !node->isFile() || !node_name || !*node_name) {
            throw std::runtime_error("This MEGA public link is not a downloadable file.");
        }
        kind = "file";
        name = node_name;
        files.push_back({node->getHandle(), node_name, node->getSize()});
    }

    if (files.empty()) {
        throw std::runtime_error("No downloadable files were found in this MEGA link.");
    }

    std::lock_guard<std::mutex> lock(output_mutex);
    std::cout << "{\"kind\":\"" << kind << "\",\"name\":\""
              << json_escape(name) << "\",\"items\":[";
    for (std::size_t index = 0; index < files.size(); ++index) {
        if (index) {
            std::cout << ",";
        }
        std::cout << "{\"handle\":\"" << files[index].handle
                  << "\",\"path\":\"" << json_escape(files[index].path)
                  << "\",\"size\":" << files[index].size << "}";
    }
    std::cout << "]}\n" << std::flush;
}

void download_file(
    const std::string& link,
    const std::string& handle_string,
    const std::filesystem::path& output_path
) {
    if (output_path.empty() || output_path.filename().empty()) {
        throw std::runtime_error("The MEGA download destination is invalid.");
    }
    std::size_t consumed = 0;
    const auto handle = std::stoull(handle_string, &consumed);
    if (consumed != handle_string.size()) {
        throw std::runtime_error("The MEGA file handle is invalid.");
    }

    auto api = create_api();
    SdkListener listener;
    std::unique_ptr<mega::MegaNode> node;
    std::string error;
    if (is_folder_link(link)) {
        listener.begin_request();
        api->loginToFolder(link.c_str(), &listener);
        if (!listener.wait_request(error)) {
            throw std::runtime_error(error);
        }
        listener.begin_request();
        api->fetchNodes(&listener);
        if (!listener.wait_request(error)) {
            throw std::runtime_error(error);
        }
        node.reset(api->getNodeByHandle(handle));
    } else {
        listener.begin_request();
        api->getPublicNode(link.c_str(), &listener);
        if (!listener.wait_request(error)) {
            throw std::runtime_error(error);
        }
        node = listener.take_request_node();
        if (node && node->getHandle() != handle) {
            throw std::runtime_error("The MEGA file handle does not match the public link.");
        }
    }
    if (!node || !node->isFile()) {
        throw std::runtime_error("The selected MEGA file is no longer available.");
    }

    auto cancel_token = std::unique_ptr<mega::MegaCancelToken>(
        mega::MegaCancelToken::createInstance()
    );
    if (!cancel_token) {
        throw std::runtime_error("The MEGA SDK could not initialize download cancellation.");
    }
    std::signal(SIGINT, handle_interrupt);
    listener.begin_transfer();
    std::string parent = output_path.parent_path().string();
    if (parent.empty() || parent.back() != '/') {
        parent += '/';
    }
    const std::string filename = output_path.filename().string();
    api->startDownload(
        node.get(),
        parent.c_str(),
        filename.c_str(),
        nullptr,
        false,
        cancel_token.get(),
        mega::MegaTransfer::COLLISION_CHECK_ALWAYSERROR,
        mega::MegaTransfer::COLLISION_RESOLUTION_OVERWRITE,
        false,
        &listener
    );
    if (!listener.wait_transfer(cancel_token.get(), error)) {
        throw std::runtime_error(error);
    }
}

} // namespace

int main(int argc, char** argv) {
    try {
        if (argc == 2 && std::string(argv[1]) == "inspect") {
            inspect_link(require_stdin_link());
            return 0;
        }
        if (argc == 4 && std::string(argv[1]) == "download") {
            download_file(
                require_stdin_link(),
                argv[2],
                std::filesystem::path(argv[3])
            );
            return 0;
        }
        throw std::runtime_error("Usage: aetherdrive-mega inspect|download");
    } catch (const std::exception& error) {
        emit_error(error.what());
        return 1;
    }
}
