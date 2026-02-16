document.addEventListener('DOMContentLoaded', () => {
    // --- State Variables ---
    let currentPage = 0;
    let isLoading = false;
    let hasMore = true;
    let currentSort = 'date_desc';
    let currentSearch = '';
    let currentFolder = 'default';
    let showFavoritesOnly = false;
    let availableFolders = [];
    let currentImages = [];
    let currentImageIndex = -1;
    let selectedForCompare = [];
    let isCompareMode = false;
    let selectedImages = [];
    let contextMenu = null;
    let modalZoom = 1;
    let modalPanX = 0;
    let modalPanY = 0;
    let isModalPanning = false;
    let modalPanStartX = 0;
    let modalPanStartY = 0;
    let modalPanOriginX = 0;
    let modalPanOriginY = 0;
    let suppressNextModalStageClose = false;
    let modalPanListenersBound = false;
    const MODAL_MIN_ZOOM = 0.25;
    const MODAL_MAX_ZOOM = 6;
    const MODAL_ZOOM_STEP = 0.15;

    // --- DOM Elements ---
    const gallery = document.getElementById('gallery');
    const loadingEl = document.getElementById('loading');
    const searchInput = document.getElementById('searchInput');
    const sortSelect = document.getElementById('sortSelect');
    const folderBtn = document.getElementById('folderBtn');
    const folderDropdown = document.getElementById('folderDropdown');
    const folderList = document.getElementById('folderList');
    const addFolderBtn = document.getElementById('addFolderBtn');
    const favoritesFilterBtn = document.getElementById('favoritesFilterBtn');
    const refreshBtn = document.getElementById('refreshBtn');
    const compareModeBtn = document.getElementById('compareModeBtn');
    const compareBtn = document.getElementById('compareBtn');
    const multiSelectActions = document.getElementById('multiSelectActions');
    const themeSelect = document.getElementById('themeSelect');

    // --- Context Menu Functions ---
    function createContextMenu() {
        const menu = document.createElement('div');
        menu.id = 'contextMenu';
        menu.className = 'context-menu';
        document.body.appendChild(menu);
        return menu;
    }

    function showContextMenu(e, imageData, card) {
        e.preventDefault();

        if (!contextMenu) {
            contextMenu = createContextMenu();
        }

        const isMultiSelect = selectedImages.length > 1;
        const isFavorite = imageData.is_favorite;

        if (isMultiSelect) {
            contextMenu.innerHTML = `
                <div class="context-menu-item" data-action="delete-selected">
                    <span class="ui-icon">■</span> Delete Selected (${selectedImages.length})
                </div>
                <div class="context-menu-item" data-action="favorite-selected">
                    <span class="ui-icon">★</span> Add to Favorites
                </div>
                <div class="context-menu-item" data-action="unfavorite-selected">
                    <span class="ui-icon">☆</span> Remove from Favorites
                </div>
                <div class="context-menu-divider"></div>
                <div class="context-menu-item" data-action="clear-selection">
                    <span class="ui-icon">✕</span> Clear Selection
                </div>
            `;
        } else {
            const imagePath = `/gemini-image-browser/thumbnail?filename=${encodeURIComponent(imageData.path)}&folder=${imageData.folder_id}`;
            contextMenu.innerHTML = `
                <div class="context-menu-item" data-action="open">
                    <span class="ui-icon">◆</span> Open
                </div>
                <div class="context-menu-item" data-action="copy-image">
                    <span class="ui-icon">▣</span> Copy Image
                </div>
                <div class="context-menu-item" data-action="copy-path">
                    <span class="ui-icon">▦</span> Copy Path
                </div>
                <div class="context-menu-divider"></div>
                <div class="context-menu-item" data-action="toggle-favorite">
                    <span class="ui-icon">${isFavorite ? '☆' : '★'}</span> ${isFavorite ? 'Remove from' : 'Add to'} Favorites
                </div>
                <div class="context-menu-divider"></div>
                <div class="context-menu-item context-menu-item-danger" data-action="delete">
                    <span class="ui-icon">■</span> Delete
                </div>
            `;
        }

        const offsetX = 12;
        const offsetY = 12;
        contextMenu.style.display = 'block';
        contextMenu.style.left = (e.pageX + offsetX) + 'px';
        contextMenu.style.top = (e.pageY + offsetY) + 'px';

        setTimeout(() => {
            const rect = contextMenu.getBoundingClientRect();
            const viewportWidth = window.innerWidth;
            const viewportHeight = window.innerHeight;

            if (rect.right > viewportWidth) {
                contextMenu.style.left = (e.pageX - rect.width - offsetX) + 'px';
            }
            if (rect.bottom > viewportHeight) {
                contextMenu.style.top = (e.pageY - rect.height - offsetY) + 'px';
            }
        }, 0);

        const oldMenu = contextMenu.cloneNode(true);
        contextMenu.parentNode.replaceChild(oldMenu, contextMenu);
        contextMenu = oldMenu;

        contextMenu.querySelectorAll('.context-menu-item').forEach(item => {
            item.addEventListener('click', async () => {
                const action = item.getAttribute('data-action');
                await handleContextMenuAction(action, imageData, card);
                hideContextMenu();
            });
        });

        setTimeout(() => {
            document.addEventListener('click', hideContextMenu, { once: true });
        }, 0);
    }

    function hideContextMenu() {
        if (contextMenu) {
            contextMenu.style.display = 'none';
        }
    }

    function getModalImageElement() {
        return document.getElementById('modalZoomImage');
    }

    function getModalZoomStage() {
        return document.getElementById('modalZoomStage');
    }

    function clampModalZoom(value) {
        return Math.min(MODAL_MAX_ZOOM, Math.max(MODAL_MIN_ZOOM, value));
    }

    function clampModalPan() {
        const image = getModalImageElement();
        const stage = getModalZoomStage();
        if (!image || !stage) return;

        if (modalZoom <= 1) {
            modalPanX = 0;
            modalPanY = 0;
            return;
        }

        const scaledWidth = image.clientWidth * modalZoom;
        const scaledHeight = image.clientHeight * modalZoom;
        const maxPanX = Math.max(0, (scaledWidth - stage.clientWidth) / 2);
        const maxPanY = Math.max(0, (scaledHeight - stage.clientHeight) / 2);

        modalPanX = Math.min(maxPanX, Math.max(-maxPanX, modalPanX));
        modalPanY = Math.min(maxPanY, Math.max(-maxPanY, modalPanY));
    }

    function applyModalZoom() {
        const image = getModalImageElement();
        if (!image) return;

        clampModalPan();
        image.style.transform = `translate(${modalPanX}px, ${modalPanY}px) scale(${modalZoom})`;
        image.style.cursor = isModalPanning ? 'grabbing' : (modalZoom > 1 ? 'grab' : 'default');

        const zoomLabel = document.getElementById('modalZoomLevel');
        if (zoomLabel) {
            zoomLabel.textContent = `${Math.round(modalZoom * 100)}%`;
        }
    }

    function setModalZoom(nextZoom) {
        modalZoom = clampModalZoom(nextZoom);
        if (modalZoom <= 1) {
            modalPanX = 0;
            modalPanY = 0;
        }
        applyModalZoom();
    }

    function resetModalZoom() {
        modalPanX = 0;
        modalPanY = 0;
        setModalZoom(1);
    }

    function initModalZoomControls() {
        const zoomInBtn = document.getElementById('modalZoomIn');
        const zoomOutBtn = document.getElementById('modalZoomOut');
        const zoomResetBtn = document.getElementById('modalZoomReset');
        const zoomStage = document.getElementById('modalZoomStage');

        if (!zoomInBtn || !zoomOutBtn || !zoomResetBtn || !zoomStage || !getModalImageElement()) {
            return;
        }

        resetModalZoom();

        zoomInBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            setModalZoom(modalZoom + MODAL_ZOOM_STEP);
        });

        zoomOutBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            setModalZoom(modalZoom - MODAL_ZOOM_STEP);
        });

        zoomResetBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            resetModalZoom();
        });

        zoomStage.addEventListener('wheel', (e) => {
            e.preventDefault();
            const zoomDelta = e.deltaY < 0 ? MODAL_ZOOM_STEP : -MODAL_ZOOM_STEP;
            setModalZoom(modalZoom + zoomDelta);
        }, { passive: false });

        zoomStage.addEventListener('mousedown', (e) => {
            if (e.button !== 0 || modalZoom <= 1) return;

            isModalPanning = true;
            modalPanStartX = e.clientX;
            modalPanStartY = e.clientY;
            modalPanOriginX = modalPanX;
            modalPanOriginY = modalPanY;
            suppressNextModalStageClose = false;
            applyModalZoom();
            e.preventDefault();
        });

        if (!modalPanListenersBound) {
            document.addEventListener('mousemove', (e) => {
                if (!isModalPanning) return;

                const dx = e.clientX - modalPanStartX;
                const dy = e.clientY - modalPanStartY;
                if (Math.abs(dx) > 2 || Math.abs(dy) > 2) {
                    suppressNextModalStageClose = true;
                }

                modalPanX = modalPanOriginX + dx;
                modalPanY = modalPanOriginY + dy;
                applyModalZoom();
            });

            document.addEventListener('mouseup', () => {
                if (!isModalPanning) return;
                isModalPanning = false;
                applyModalZoom();
            });

            modalPanListenersBound = true;
        }
    }

    async function handleContextMenuAction(action, imageData, card) {
        const imagePath = `/gemini-image-browser/thumbnail?filename=${encodeURIComponent(imageData.path)}&folder=${imageData.folder_id}`;

        switch (action) {
            case 'open':
                const index = parseInt(card.dataset.index, 10);
                if (!Number.isNaN(index)) {
                    openModal(index);
                }
                break;

            case 'copy-image':
                await copyImageToClipboard(imagePath);
                break;

            case 'copy-path':
                await copyPathToClipboard(imageData);
                break;

            case 'toggle-favorite':
                const favBtn = card.querySelector('.favorite-btn');
                await toggleFavorite(imageData.path, imageData.folder_id, card, favBtn);
                break;

            case 'delete':
                await deleteImage(imageData, card);
                break;

            case 'delete-selected':
                await deleteSelectedImages();
                break;

            case 'favorite-selected':
                await favoriteSelectedImages(true);
                break;

            case 'unfavorite-selected':
                await favoriteSelectedImages(false);
                break;

            case 'clear-selection':
                clearSelection();
                break;
        }
    }

    async function copyImageToClipboard(imageUrl) {
        try {
            const response = await fetch(imageUrl);
            const blob = await response.blob();
            await navigator.clipboard.write([
                new ClipboardItem({ [blob.type]: blob })
            ]);
            showNotification('Image copied to clipboard');
        } catch (error) {
            console.error('Error copying image:', error);
            showNotification('Failed to copy image', 'error');
        }
    }

    async function copyPathToClipboard(imageData) {
        try {
            let fullPath;

            if (imageData.folder_id === 'default') {
                const defaultFolder = availableFolders.find(f => f.id === 'default');
                fullPath = defaultFolder ? `${defaultFolder.path}\\${imageData.path}` : imageData.path;
            } else {
                const folder = availableFolders.find(f => f.id === imageData.folder_id);
                fullPath = folder ? `${folder.path}\\${imageData.path}` : imageData.path;
            }

            await navigator.clipboard.writeText(fullPath);
            showNotification('Path copied to clipboard');
        } catch (error) {
            console.error('Error copying path:', error);
            showNotification('Failed to copy path', 'error');
        }
    }

    async function copyPromptToClipboard(promptText, promptLabel = 'Prompt') {
        if (!promptText) return;

        try {
            await navigator.clipboard.writeText(promptText);
            showNotification(`${promptLabel} copied to clipboard`);
        } catch (error) {
            console.error(`Error copying ${promptLabel}:`, error);
            showNotification(`Failed to copy ${promptLabel.toLowerCase()}`, 'error');
        }
    }

    async function deleteImage(imageData, card) {
        if (!confirm(`Delete ${imageData.name}?`)) return;

        try {
            const response = await fetch('/gemini-image-browser/delete', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    filename: imageData.path,
                    folder_id: imageData.folder_id
                })
            });
            const data = await response.json();

            if (data.success) {
                card.remove();
                removeImageFromState(imageData);
                showNotification('Image deleted');
            } else {
                throw new Error(data.error || 'Delete failed');
            }
        } catch (error) {
            console.error('Error deleting image:', error);
            showNotification('Failed to delete image', 'error');
        }
    }

    async function deleteSelectedImages() {
        if (!confirm(`Delete ${selectedImages.length} selected images?`)) return;

        const totalToDelete = selectedImages.length;
        const imagesToDelete = [...selectedImages];
        let successCount = 0;
        for (const imageData of imagesToDelete) {
            try {
                const response = await fetch('/gemini-image-browser/delete', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        filename: imageData.path,
                        folder_id: imageData.folder_id
                    })
                });
                const data = await response.json();

                if (data.success) {
                    const card = document.querySelector(`[data-filename="${imageData.path}"][data-folder-id="${imageData.folder_id}"]`);
                    if (card) card.remove();
                    removeImageFromState(imageData);
                    successCount++;
                }
            } catch (error) {
                console.error('Error deleting image:', error);
            }
        }

        clearSelection();
        showNotification(`Deleted ${successCount} of ${totalToDelete} images`);
    }

    async function favoriteSelectedImages(addToFavorites) {
        for (const imageData of selectedImages) {
            const card = document.querySelector(`[data-filename="${imageData.path}"][data-folder-id="${imageData.folder_id}"]`);
            if (!card) continue;

            const isFavorite = card.classList.contains('favorited');
            const shouldToggle = (addToFavorites && !isFavorite) || (!addToFavorites && isFavorite);

            if (shouldToggle) {
                const favBtn = card.querySelector('.favorite-btn');
                await toggleFavorite(imageData.path, imageData.folder_id, card, favBtn);
            }
        }

        showNotification(`Updated favorites for ${selectedImages.length} images`);
    }

    function showNotification(message, type = 'success') {
        const notification = document.createElement('div');
        notification.className = `notification notification-${type}`;
        notification.textContent = message;
        document.body.appendChild(notification);

        setTimeout(() => notification.classList.add('show'), 10);
        setTimeout(() => {
            notification.classList.remove('show');
            setTimeout(() => notification.remove(), 300);
        }, 2000);
    }

    function isSameImage(a, b) {
        return a.path === b.path && a.folder_id === b.folder_id;
    }

    function getImageKey(path, folderId) {
        return `${folderId}::${path}`;
    }

    function rebuildGalleryIndexes() {
        const indexByKey = new Map(
            currentImages.map((img, index) => [getImageKey(img.path, img.folder_id), index])
        );

        document.querySelectorAll('.image-card').forEach((card) => {
            const key = getImageKey(card.dataset.filename, card.dataset.folderId);
            const index = indexByKey.get(key);
            if (typeof index === 'number') {
                card.dataset.index = index;
            } else {
                card.removeAttribute('data-index');
            }
        });
    }

    function removeImageFromState(imageData) {
        const removedIndex = currentImages.findIndex((img) => isSameImage(img, imageData));
        if (removedIndex === -1) return;

        currentImages.splice(removedIndex, 1);

        if (currentImageIndex > removedIndex) {
            currentImageIndex--;
        } else if (currentImageIndex >= currentImages.length) {
            currentImageIndex = currentImages.length - 1;
        }

        selectedImages = selectedImages.filter((img) => !isSameImage(img, imageData));
        selectedForCompare = selectedForCompare.filter((img) => !isSameImage(img, imageData));

        updateMultiSelectActions();
        compareBtn.style.display = selectedForCompare.length === 2 ? 'block' : 'none';

        if (selectedForCompare.length === 1) {
            const remaining = selectedForCompare[0];
            const remainingCard = document.querySelector(`[data-filename="${remaining.path}"][data-folder-id="${remaining.folder_id}"]`);
            if (remainingCard) {
                remainingCard.classList.remove('compare-second');
                remainingCard.classList.add('compare-first');
            }
        }

        rebuildGalleryIndexes();
    }

    function toggleImageSelection(imageData, card, event) {
        const index = selectedImages.findIndex(
            img => img.path === imageData.path && img.folder_id === imageData.folder_id
        );

        if (index !== -1) {
            selectedImages.splice(index, 1);
            card.classList.remove('selected');
        } else {
            selectedImages.push(imageData);
            card.classList.add('selected');
        }

        updateMultiSelectActions();
    }

    function clearSelection() {
        selectedImages = [];
        document.querySelectorAll('.image-card.selected').forEach(card => {
            card.classList.remove('selected');
        });
        updateMultiSelectActions();
    }

    function updateMultiSelectActions() {
        if (selectedImages.length > 0) {
            multiSelectActions.style.display = 'flex';
            document.getElementById('selectedCount').textContent = selectedImages.length;
        } else {
            multiSelectActions.style.display = 'none';
        }
    }

    // CONTINUATION FROM PART 1...

    async function loadImages(reset = false) {
        if (isLoading || (!hasMore && !reset)) return;
        isLoading = true;
        if (reset) {
            currentPage = 0;
            hasMore = true;
            gallery.innerHTML = '';
            clearSelection();
        }
        loadingEl.style.display = 'block';

        const params = new URLSearchParams({
            page: currentPage,
            per_page: 50,
            sort: currentSort,
            search: currentSearch,
            folder: showFavoritesOnly ? 'all' : currentFolder,
            favorites: showFavoritesOnly,
        });

        console.log(`Gemini Browser: Requesting images with params: ${params.toString()}`);

        try {
            const response = await fetch(`/gemini-image-browser/list?${params.toString()}`);
            const data = await response.json();

            console.log("Gemini Browser: Received data from server:", data);

            if (data.error) { throw new Error(data.error); }

            hasMore = data.has_more;
            if (reset) {
                availableFolders = data.folders;
                updateFolderList();
            }

            if (data.images.length === 0 && reset) {
                gallery.innerHTML = '<p>No images found with current filters.</p>';
            }

            console.log(`Gemini Browser: Rendering ${data.images.length} images.`);

            data.images.forEach((img, index) => {
                const globalIndex = reset ? index : currentImages.length + index;

                const card = document.createElement('div');
                card.className = 'image-card' + (img.is_favorite ? ' favorited' : '');
                card.dataset.filename = img.path;
                card.dataset.folderId = img.folder_id;
                card.dataset.index = globalIndex;

                const favBtn = document.createElement('button');
                favBtn.className = 'favorite-btn' + (img.is_favorite ? ' favorited' : '');
                favBtn.innerHTML = '★';
                favBtn.title = 'Toggle favorite';
                favBtn.onclick = (e) => {
                    e.stopPropagation();
                    toggleFavorite(img.path, img.folder_id, card, favBtn);
                };
                card.appendChild(favBtn);

                const isVideo = /\.(mp4|webm|mov)$/i.test(img.name);
                const isAudio = /\.(mp3|wav|ogg|flac|m4a)$/i.test(img.name);
                let mediaHtml;
                if (isVideo) {
                    mediaHtml = `<video src="/gemini-image-browser/thumbnail?filename=${encodeURIComponent(img.path)}&folder=${img.folder_id}" muted loop onmouseover="this.play()" onmouseout="this.pause()"></video>`;
                } else if (isAudio) {
                    mediaHtml = `<div class="audio-thumbnail"><div class="audio-icon">♪</div><audio src="/gemini-image-browser/thumbnail?filename=${encodeURIComponent(img.path)}&folder=${img.folder_id}" preload="metadata"></audio></div>`;
                } else {
                    mediaHtml = `<img src="/gemini-image-browser/thumbnail?filename=${encodeURIComponent(img.path)}&folder=${img.folder_id}" alt="${img.name}" loading="lazy">`;
                }

                const mediaEl = document.createElement('div');
                mediaEl.innerHTML = mediaHtml;
                mediaEl.onclick = (e) => {
                    const cardIndex = parseInt(card.dataset.index, 10);
                    if (Number.isNaN(cardIndex)) return;

                    if (e.ctrlKey || e.metaKey) {
                        toggleImageSelection(img, card, e);
                    } else if (isCompareMode) {
                        toggleCompareSelection(cardIndex, card);
                    } else {
                        openModal(cardIndex);
                    }
                };

                mediaEl.addEventListener('contextmenu', (e) => {
                    showContextMenu(e, img, card);
                });

                card.appendChild(mediaEl);

                if (isVideo) {
                    const videoIndicator = document.createElement('div');
                    videoIndicator.className = 'video-indicator';
                    videoIndicator.textContent = 'VIDEO';
                    card.appendChild(videoIndicator);
                } else if (isAudio) {
                    const audioIndicator = document.createElement('div');
                    audioIndicator.className = 'audio-indicator';
                    audioIndicator.textContent = 'AUDIO';
                    card.appendChild(audioIndicator);
                }

                const infoEl = document.createElement('div');
                infoEl.className = 'image-info';
                infoEl.innerHTML = `<div class="image-name" title="${img.name}">${img.name}</div>`;
                card.appendChild(infoEl);

                gallery.appendChild(card);
            });

            if (reset) {
                currentImages = data.images;
            } else {
                currentImages = [...currentImages, ...data.images];
            }
            currentPage++;
        } catch (error) {
            console.error('Gemini Browser: Error loading images:', error);
            gallery.innerHTML = `<p>Error loading images. Check browser console (F12) for details.</p>`;
        } finally {
            isLoading = false;
            loadingEl.style.display = hasMore ? 'block' : 'none';
        }
    }

    async function toggleFavorite(filename, folderId, card, btn) {
        try {
            const response = await fetch('/gemini-image-browser/favorite', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ filename, folder_id: folderId })
            });
            const data = await response.json();
            if (data.success) {
                const isFavorite = data.is_favorite;
                card.classList.toggle('favorited', isFavorite);
                btn.classList.toggle('favorited', isFavorite);
                if (showFavoritesOnly && !isFavorite) {
                    card.style.display = 'none';
                }
            }
        } catch (error) { console.error('Error toggling favorite:', error); }
    }

    function toggleCompareSelection(index, card) {
        const imageData = currentImages[index];
        const compareIndex = selectedForCompare.findIndex(
            img => img.path === imageData.path && img.folder_id === imageData.folder_id
        );

        if (compareIndex !== -1) {
            selectedForCompare.splice(compareIndex, 1);
            card.classList.remove('selected-for-compare', 'compare-first', 'compare-second');

            if (selectedForCompare.length === 1) {
                const remainingCard = document.querySelector(`[data-filename="${selectedForCompare[0].path}"][data-folder-id="${selectedForCompare[0].folder_id}"]`);
                if (remainingCard) {
                    remainingCard.classList.remove('compare-second');
                    remainingCard.classList.add('compare-first');
                }
            }
        } else if (selectedForCompare.length < 2) {
            selectedForCompare.push(imageData);
            card.classList.add('selected-for-compare');

            if (selectedForCompare.length === 1) {
                card.classList.add('compare-first');
            } else if (selectedForCompare.length === 2) {
                card.classList.add('compare-second');
            }
        } else {
            alert('You can only compare 2 images at a time. Deselect one first.');
        }

        compareBtn.style.display = selectedForCompare.length === 2 ? 'block' : 'none';
    }

    function openCompareModal() {
        if (selectedForCompare.length !== 2) return;

        const modal = document.getElementById('compareModal');
        const container = document.getElementById('compareContainer');
        const img1 = selectedForCompare[0];
        const img2 = selectedForCompare[1];

        const url1 = `/gemini-image-browser/thumbnail?filename=${encodeURIComponent(img1.path)}&folder=${img1.folder_id}`;
        const url2 = `/gemini-image-browser/thumbnail?filename=${encodeURIComponent(img2.path)}&folder=${img2.folder_id}`;

        container.innerHTML = `
            <div class="compare-wrapper">
                <div class="compare-labels">
                    <div class="compare-label compare-label-left">${escapeHtml(img1.name)}</div>
                    <div class="compare-label compare-label-right">${escapeHtml(img2.name)}</div>
                </div>
                <div class="compare-image-container">
                    <img src="${url2}" alt="Image 2" class="compare-img compare-img-back">
                    <div class="compare-img-front-wrapper">
                        <img src="${url1}" alt="Image 1" class="compare-img compare-img-front">
                    </div>
                    <div class="compare-slider" id="compareSlider">
                        <div class="compare-slider-handle"></div>
                    </div>
                </div>
            </div>
        `;

        modal.classList.add('active');
        document.body.style.overflow = 'hidden';

        initCompareSlider();
    }

    function initCompareSlider() {
        const slider = document.getElementById('compareSlider');
        const frontWrapper = document.querySelector('.compare-img-front-wrapper');
        const container = document.querySelector('.compare-image-container');
        let isDragging = false;

        function updateSlider(clientX) {
            const rect = container.getBoundingClientRect();
            let x = clientX - rect.left;
            x = Math.max(0, Math.min(x, rect.width));
            const percentage = (x / rect.width) * 100;

            slider.style.left = percentage + '%';
            frontWrapper.style.width = percentage + '%';
        }

        slider.addEventListener('mousedown', (e) => {
            isDragging = true;
            e.preventDefault();
        });

        document.addEventListener('mousemove', (e) => {
            if (!isDragging) return;
            updateSlider(e.clientX);
        });

        document.addEventListener('mouseup', () => {
            isDragging = false;
        });

        container.addEventListener('click', (e) => {
            if (!isDragging) {
                updateSlider(e.clientX);
            }
        });

        slider.addEventListener('touchstart', (e) => {
            isDragging = true;
            e.preventDefault();
        });

        document.addEventListener('touchmove', (e) => {
            if (!isDragging) return;
            updateSlider(e.touches[0].clientX);
        });

        document.addEventListener('touchend', () => {
            isDragging = false;
        });
    }

    window.closeCompareModal = () => {
        const modal = document.getElementById('compareModal');
        modal.classList.remove('active');
        document.body.style.overflow = '';
        document.getElementById('compareContainer').innerHTML = '';
    };

    function syncGalleryScroll(index) {
        const card = document.querySelector(`.image-card[data-index="${index}"]`);
        if (!card) return;

        card.scrollIntoView({
            behavior: "instant",
            block: "center",
            inline: "nearest"
        });
    }

    async function openModal(index) {
        currentImageIndex = index;
        const imageData = currentImages[index];
        const imagePath = imageData.path;
        const folderId = imageData.folder_id;

        const modal = document.getElementById('modal');
        const mediaContainer = document.getElementById('modalMediaContainer');
        const metadataPanel = document.getElementById('metadataPanel');
        const navPrev = document.getElementById('modalNavPrev');
        const navNext = document.getElementById('modalNavNext');

        navPrev.style.display = index > 0 ? 'block' : 'none';
        navNext.style.display = (index < currentImages.length - 1 || hasMore) ? 'block' : 'none';

        const isVideo = /\.(mp4|webm|mov)$/i.test(imagePath);
        const isAudio = /\.(mp3|wav|ogg|flac|m4a)$/i.test(imagePath);
        const url = `/gemini-image-browser/thumbnail?filename=${encodeURIComponent(imagePath)}&folder=${folderId}`;

        if (isVideo) {
            mediaContainer.innerHTML = `<video src="${url}" controls autoplay loop></video>`;
        } else if (isAudio) {
            mediaContainer.innerHTML = `<div class="audio-modal-container"><div class="audio-modal-icon">♪</div><audio src="${url}" controls autoplay></audio></div>`;
        } else {
            mediaContainer.innerHTML = `
                <div class="modal-zoom-controls">
                    <button class="modal-zoom-btn" id="modalZoomOut" title="Zoom Out">-</button>
                    <button class="modal-zoom-btn modal-zoom-btn-reset" id="modalZoomReset" title="Reset Zoom"><span id="modalZoomLevel">100%</span></button>
                    <button class="modal-zoom-btn" id="modalZoomIn" title="Zoom In">+</button>
                </div>
                <div class="modal-zoom-stage" id="modalZoomStage">
                    <img id="modalZoomImage" src="${url}" alt="">
                </div>
            `;
            initModalZoomControls();
        }

        modal.classList.add('active');
        document.body.style.overflow = 'hidden';

        syncGalleryScroll(index);

        const isFavorite = imageData.is_favorite;
        metadataPanel.innerHTML = `
            <div class="modal-header-actions">
                <button id="modalFavoriteBtn" class="modal-action-btn ${isFavorite ? 'favorite' : ''}" onclick="toggleModalFavorite()">
                    <span class="ui-icon">${isFavorite ? '★' : '☆'}</span>
                    ${isFavorite ? 'Favorited' : 'Add to Favorites'}
                </button>
                <div class="modal-view-toggle">
                    <button id="metadataViewBtn" class="modal-view-btn active" onclick="switchMetadataView('metadata')">Metadata</button>
                    <button id="workflowViewBtn" class="modal-view-btn" onclick="switchMetadataView('workflow')">Workflow Nodes</button>
                </div>
            </div>
            <div class="metadata-section">
                <div class="metadata-section-header">
                    <span class="icon ui-icon">■</span> Prompts
                </div>
                <div class="metadata-section-content" id="promptsSection">
                    <p style="color: #888; margin: 0;">Loading...</p>
                </div>
            </div>
            <div class="metadata-section">
                <div class="metadata-section-header">
                    <span class="icon ui-icon">▦</span> Generation Settings
                </div>
                <div class="metadata-section-content" id="settingsSection">
                    <p style="color: #888; margin: 0;">Loading...</p>
                </div>
            </div>
            <div class="metadata-section">
                <div class="metadata-section-header">
                    <span class="icon ui-icon">▣</span> LoRAs
                </div>
                <div class="metadata-section-content" id="lorasSection">
                    <p style="color: #888; margin: 0;">Loading...</p>
                </div>
            </div>
            <div class="metadata-section">
                <div class="metadata-section-header">
                    <span class="icon ui-icon">▢</span> File Info
                </div>
                <div class="metadata-section-content" id="fileInfoSection">
                    <div class="file-info-grid">
                        <div class="file-info-item full-width">
                            <div class="label">Filename</div>
                            <div class="value">${escapeHtml(imageData.name)}</div>
                        </div>
                        <div class="file-info-item">
                            <div class="label">Size</div>
                            <div class="value">${formatFileSize(imageData.size)}</div>
                        </div>
                        <div class="file-info-item">
                            <div class="label">Modified</div>
                            <div class="value">${formatDate(imageData.modified)}</div>
                        </div>
                    </div>
                </div>
            </div>
            <div class="metadata-section">
                <div class="metadata-section-header">
                    <span class="icon ui-icon">N</span> Workflow Node Inspector
                </div>
                <div class="metadata-section-content" id="workflowNodesSection">
                    <p style="color: #888; margin: 0;">Loading...</p>
                </div>
            </div>
            <button class="modal-delete-btn" onclick="deleteCurrentImage()">Delete Image</button>
        `;

        const workflowContent = document.getElementById('workflowNodesSection');
        const workflowSection = workflowContent ? workflowContent.closest('.metadata-section') : null;
        if (workflowSection) {
            workflowSection.id = 'workflowNodesView';
            workflowSection.classList.add('workflow-section');
            workflowSection.style.display = 'none';
            const workflowHeader = workflowSection.querySelector('.metadata-section-header');
            if (workflowHeader) {
                workflowHeader.innerHTML = '<span class="icon ui-icon">N</span> Workflow Node Inspector';
            }
        }

        try {
            const metaUrl = `/gemini-image-browser/metadata?filename=${encodeURIComponent(imagePath)}&folder=${folderId}`;
            const response = await fetch(metaUrl);
            const data = await response.json();
            displayMetadata(data);
        } catch (error) {
            document.getElementById('promptsSection').innerHTML =
                `<p style="color: #dc3545;">Error loading metadata: ${error.message}</p>`;
            if (workflowContent) {
                workflowContent.innerHTML = `<p style="color: #dc3545; margin: 0;">Error loading workflow nodes: ${error.message}</p>`;
            }
        }
    }

    async function navigateModal(direction) {
        const newIndex = currentImageIndex + direction;

        if (direction === 1 && newIndex >= currentImages.length) {
            if (hasMore && !isLoading) {
                const navNext = document.getElementById('modalNavNext');
                if (navNext) navNext.style.opacity = '0.5';

                await loadImages(false);

                if (navNext) navNext.style.opacity = '1';

                if (newIndex < currentImages.length) {
                    openModal(newIndex);
                }
            }
        } else if (newIndex >= 0 && newIndex < currentImages.length) {
            openModal(newIndex);
        }
    }

    window.navigatePrev = () => navigateModal(-1);
    window.navigateNext = () => navigateModal(1);

    window.closeModal = () => {
        const modal = document.getElementById('modal');
        modal.classList.remove('active');
        document.body.style.overflow = '';
        document.getElementById('modalMediaContainer').innerHTML = '';
        modalZoom = 1;
        modalPanX = 0;
        modalPanY = 0;
        isModalPanning = false;
        suppressNextModalStageClose = false;
    };

    document.getElementById('modal').addEventListener('click', (e) => {
        if (e.target.id === 'modalZoomStage' && suppressNextModalStageClose) {
            suppressNextModalStageClose = false;
            return;
        }
        if (e.target.id === 'modal' || e.target.id === 'modalMediaContainer' || e.target.id === 'modalZoomStage') {
            closeModal();
        }
    });

    window.switchMetadataView = (view) => {
        const showWorkflow = view === 'workflow';
        const metadataSections = document.querySelectorAll('#metadataPanel .metadata-section:not(.workflow-section)');
        const workflowSection = document.getElementById('workflowNodesView');
        const metadataBtn = document.getElementById('metadataViewBtn');
        const workflowBtn = document.getElementById('workflowViewBtn');

        metadataSections.forEach((section) => {
            section.style.display = showWorkflow ? 'none' : '';
        });
        if (workflowSection) {
            workflowSection.style.display = showWorkflow ? '' : 'none';
        }
        if (metadataBtn) metadataBtn.classList.toggle('active', !showWorkflow);
        if (workflowBtn) workflowBtn.classList.toggle('active', showWorkflow);
    };

    function renderWorkflowNodes(workflowNodes) {
        const container = document.getElementById('workflowNodesSection');
        if (!container) return;

        if (!Array.isArray(workflowNodes) || workflowNodes.length === 0) {
            container.innerHTML = '<p class="workflow-empty">No workflow node data found in this file.</p>';
            return;
        }

        let html = '';
        workflowNodes.forEach((node) => {
            const nodeType = escapeHtml(node.type || 'Unknown');
            const nodeId = escapeHtml(node.id || 'N/A');
            const params = Array.isArray(node.params) ? node.params : [];

            let paramsHtml = '';
            if (params.length === 0) {
                paramsHtml = '<li><span class="workflow-param-value">No parameters</span></li>';
            } else {
                params.forEach((param) => {
                    const paramName = escapeHtml(param.name || 'param');
                    const paramValue = escapeHtml(String(param.value ?? ''));
                    paramsHtml += `<li><span class="workflow-param-name">${paramName}</span><span class="workflow-param-value">${paramValue}</span></li>`;
                });
            }

            html += `
                <div class="workflow-node-item">
                    <div class="workflow-node-header">${nodeType}<span class="workflow-node-meta">ID: ${nodeId}</span></div>
                    <ul class="workflow-node-params">${paramsHtml}</ul>
                </div>
            `;
        });

        container.innerHTML = html;
    }

    function displayMetadata(data) {
        const { parsed, dimensions } = data;

        if (!parsed) {
            document.getElementById('promptsSection').innerHTML = '<p style="color: #888;">Could not parse metadata.</p>';
            return;
        }

        let promptsHtml = '';
        if (parsed.prompt || parsed.negative_prompt) {
            if (parsed.prompt) {
                promptsHtml += `
                    <div class="metadata-item">
                        <div class="metadata-label-row">
                            <div class="metadata-label">Positive Prompt</div>
                            <button class="metadata-copy-btn" type="button" data-prompt-kind="positive">Copy</button>
                        </div>
                        <div class="metadata-prompt">${escapeHtml(parsed.prompt)}</div>
                    </div>
                `;
            }
            if (parsed.negative_prompt) {
                promptsHtml += `
                    <div class="metadata-item">
                        <div class="metadata-label-row">
                            <div class="metadata-label">Negative Prompt</div>
                            <button class="metadata-copy-btn" type="button" data-prompt-kind="negative">Copy</button>
                        </div>
                        <div class="metadata-prompt">${escapeHtml(parsed.negative_prompt)}</div>
                    </div>
                `;
            }
        } else {
            promptsHtml = '<p style="color: #888; margin: 0;">No prompts found.</p>';
        }
        document.getElementById('promptsSection').innerHTML = promptsHtml;

        document.querySelectorAll('#promptsSection .metadata-copy-btn').forEach((btn) => {
            btn.addEventListener('click', async (e) => {
                e.stopPropagation();
                const promptKind = btn.dataset.promptKind;
                const promptText = promptKind === 'negative' ? parsed.negative_prompt : parsed.prompt;
                const promptLabel = promptKind === 'negative' ? 'Negative prompt' : 'Positive prompt';
                await copyPromptToClipboard(promptText, promptLabel);
            });
        });

        let settingsHtml = '<div class="settings-grid">';

        const size = dimensions ? `${dimensions.width} × ${dimensions.height}` : (parsed.width && parsed.height ? `${parsed.width} × ${parsed.height}` : null);

        const settings = [
            { label: 'Model', value: parsed.model, fullWidth: true },
            { label: 'Seed', value: parsed.seed },
            { label: 'Steps', value: parsed.steps },
            { label: 'CFG Scale', value: parsed.cfg },
            { label: 'Sampler', value: parsed.sampler },
            { label: 'Scheduler', value: parsed.scheduler },
            { label: 'Size', value: size }
        ];

        settings.forEach(setting => {
            if (setting.value) {
                settingsHtml += `
                    <div class="metadata-item ${setting.fullWidth ? 'full-width' : ''}">
                        <div class="metadata-label">${setting.label}</div>
                        <div class="metadata-value">${escapeHtml(setting.value)}</div>
                    </div>
                `;
            }
        });
        settingsHtml += '</div>';
        document.getElementById('settingsSection').innerHTML = settingsHtml;

        let lorasHtml = '';
        if (parsed.loras && parsed.loras.length > 0) {
            parsed.loras.forEach(lora => {
                lorasHtml += `
                    <div class="lora-item">
                        <div class="lora-name">${escapeHtml(lora.name)}</div>
                        <div class="lora-strength">Model: ${lora.strength_model} | CLIP: ${lora.strength_clip}</div>
                    </div>
                `;
            });
        } else {
            lorasHtml = '<p style="color: #888; margin: 0;">No LoRAs used.</p>';
        }
        document.getElementById('lorasSection').innerHTML = lorasHtml;

        renderWorkflowNodes(data.workflow_nodes || []);
    }

    window.toggleSection = (header) => {
        return;
    };

    window.toggleModalFavorite = async () => {
        const imageData = currentImages[currentImageIndex];
        const card = document.querySelector(`[data-filename="${imageData.path}"][data-folder-id="${imageData.folder_id}"]`);
        const favBtn = card?.querySelector('.favorite-btn');

        await toggleFavorite(imageData.path, imageData.folder_id, card, favBtn);

        const modalBtn = document.getElementById('modalFavoriteBtn');
        const isFavorite = card?.classList.contains('favorited');
        if (modalBtn) {
            modalBtn.className = `modal-action-btn ${isFavorite ? 'favorite' : ''}`;
            modalBtn.innerHTML = `<span class="ui-icon">${isFavorite ? '★' : '☆'}</span> ${isFavorite ? 'Favorited' : 'Add to Favorites'}`;
        }

        currentImages[currentImageIndex].is_favorite = isFavorite;
    };

    window.deleteCurrentImage = async () => {
        const imageData = currentImages[currentImageIndex];
        const card = document.querySelector(`[data-filename="${imageData.path}"][data-folder-id="${imageData.folder_id}"]`);

        if (!confirm(`Delete ${imageData.name}?`)) return;

        try {
            const response = await fetch('/gemini-image-browser/delete', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    filename: imageData.path,
                    folder_id: imageData.folder_id
                })
            });
            const data = await response.json();

            if (data.success) {
                if (card) card.remove();
                removeImageFromState(imageData);
                closeModal();
                showNotification('Image deleted');
            } else {
                throw new Error(data.error || 'Delete failed');
            }
        } catch (error) {
            console.error('Error deleting image:', error);
            showNotification('Failed to delete image', 'error');
        }
    };

    function formatFileSize(bytes) {
        if (bytes === 0) return '0 B';
        const k = 1024;
        const sizes = ['B', 'KB', 'MB', 'GB'];
        const i = Math.floor(Math.log(bytes) / Math.log(k));
        return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
    }

    function formatDate(timestamp) {
        const date = new Date(timestamp * 1000);
        return date.toLocaleDateString() + ' ' + date.toLocaleTimeString();
    }

    function updateFolderList() {
        folderList.innerHTML = '';
        availableFolders.forEach(folder => {
            const item = document.createElement('div');
            item.className = 'folder-item' + (folder.id === currentFolder ? ' active' : '');

            const content = document.createElement('div');
            content.className = 'folder-item-content';
            content.textContent = folder.name;
            content.onclick = () => {
                currentFolder = folder.id;
                folderDropdown.classList.remove('active');
                loadImages(true);
            };
            item.appendChild(content);

            // Add delete button only for custom folders (not the default)
            if (folder.id !== 'default') {
                const deleteBtn = document.createElement('button');
                deleteBtn.className = 'folder-item-delete';
                deleteBtn.innerHTML = '■';
                deleteBtn.title = 'Delete folder';
                deleteBtn.onclick = async (e) => {
                    e.stopPropagation();
                    if (confirm(`Remove "${folder.name}" from the list? (Files won't be deleted)`)) {
                        await removeFolder(folder.id);
                    }
                };
                item.appendChild(deleteBtn);
            }

            folderList.appendChild(item);
        });
    }

    async function addNewFolder() {
        const nameInput = document.getElementById('folderName');
        const pathInput = document.getElementById('folderPath');
        if (!nameInput.value || !pathInput.value) {
            alert('Please provide a name and path.');
            return;
        }
        try {
            const response = await fetch('/gemini-image-browser/add_folder', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name: nameInput.value, path: pathInput.value })
            });
            const data = await response.json();
            if (data.success) {
                nameInput.value = '';
                pathInput.value = '';
                folderDropdown.classList.remove('active');
                loadImages(true);
            } else {
                throw new Error(data.error);
            }
        } catch (error) {
            alert(`Error adding folder: ${error.message}`);
        }
    }
    async function removeFolder(folderId) {
        try {
            const response = await fetch('/gemini-image-browser/remove_folder', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ folder_id: folderId })
            });
            const data = await response.json();

            if (data.success) {
                // If we're currently viewing the deleted folder, switch to default
                if (currentFolder === folderId) {
                    currentFolder = 'default';
                    loadImages(true);
                } else {
                    // Just reload the folder list
                    const listResponse = await fetch('/gemini-image-browser/list?page=0&per_page=1');
                    const listData = await listResponse.json();
                    availableFolders = listData.folders;
                    updateFolderList();
                }
                showNotification('Folder removed from list');
            } else {
                throw new Error(data.error || 'Remove failed');
            }
        } catch (error) {
            console.error('Error removing folder:', error);
            showNotification('Failed to remove folder', 'error');
        }
    }

    function debounce(func, delay) {
        let timeout;
        return function (...args) {
            clearTimeout(timeout);
            timeout = setTimeout(() => func.apply(this, args), delay);
        };
    }

    function applyTheme(theme) {
        const normalized = theme === 'nier' ? 'nier' : 'dark';
        document.body.dataset.theme = normalized;
    }

    searchInput.addEventListener('input', debounce((e) => {
        currentSearch = e.target.value;
        loadImages(true);
    }, 300));

    sortSelect.addEventListener('change', (e) => {
        currentSort = e.target.value;
        loadImages(true);
    });

    favoritesFilterBtn.addEventListener('click', () => {
        showFavoritesOnly = !showFavoritesOnly;
        favoritesFilterBtn.classList.toggle('active', showFavoritesOnly);
        loadImages(true);
    });

    refreshBtn.addEventListener('click', () => loadImages(true));
    addFolderBtn.addEventListener('click', addNewFolder);

    if (themeSelect) {
        const savedTheme = localStorage.getItem('geminiImageBrowserTheme') || 'dark';
        themeSelect.value = savedTheme;
        applyTheme(savedTheme);

        themeSelect.addEventListener('change', (e) => {
            const selected = e.target.value;
            applyTheme(selected);
            localStorage.setItem('geminiImageBrowserTheme', selected);
        });
    }

    compareModeBtn.addEventListener('click', () => {
        isCompareMode = !isCompareMode;
        compareModeBtn.classList.toggle('active', isCompareMode);

        if (!isCompareMode) {
            selectedForCompare = [];
            compareBtn.style.display = 'none';
            document.querySelectorAll('.selected-for-compare').forEach(card => {
                card.classList.remove('selected-for-compare', 'compare-first', 'compare-second');
            });
        }
    });

    compareBtn.addEventListener('click', openCompareModal);

    document.getElementById('deleteSelectedBtn').addEventListener('click', deleteSelectedImages);
    document.getElementById('favoriteSelectedBtn').addEventListener('click', () => favoriteSelectedImages(true));
    document.getElementById('unfavoriteSelectedBtn').addEventListener('click', () => favoriteSelectedImages(false));
    document.getElementById('clearSelectionBtn').addEventListener('click', clearSelection);

    folderBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        folderDropdown.classList.toggle('active');
    });

    document.addEventListener('click', (e) => {
        if (!folderDropdown.contains(e.target) && !folderBtn.contains(e.target)) {
            folderDropdown.classList.remove('active');
        }
        hideContextMenu();
    });

    // OPTIMIZED SCROLL HANDLING - THE FIX FOR SMOOTH SCROLLING
    let ticking = false;

    function checkScrollPosition() {
        const scrollY = window.scrollY;
        const windowHeight = window.innerHeight;
        const documentHeight = document.documentElement.scrollHeight;

        if (!isLoading && hasMore && (scrollY + windowHeight >= documentHeight - 500)) {
            loadImages();
        }

        ticking = false;
    }

    window.addEventListener('scroll', () => {
        if (!ticking) {
            window.requestAnimationFrame(checkScrollPosition);
            ticking = true;
        }
    }, { passive: true });

    document.addEventListener('keydown', e => {
        const modal = document.getElementById('modal');
        const isModalOpen = modal && modal.classList.contains('active');

        if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') {
            return;
        }

        if (e.key === 'Escape') {
            if (isModalOpen) {
                closeModal();
            }
            hideContextMenu();
            if (selectedImages.length > 0) {
                clearSelection();
            }
        } else if (isModalOpen && (e.key === 'ArrowLeft' || e.key === 'ArrowRight')) {
            e.preventDefault();
            if (e.key === 'ArrowLeft') {
                navigatePrev();
            } else if (e.key === 'ArrowRight') {
                navigateNext();
            }
        } else if (isModalOpen && (e.key === '+' || e.key === '=')) {
            if (getModalImageElement()) {
                e.preventDefault();
                setModalZoom(modalZoom + MODAL_ZOOM_STEP);
            }
        } else if (isModalOpen && (e.key === '-' || e.key === '_')) {
            if (getModalImageElement()) {
                e.preventDefault();
                setModalZoom(modalZoom - MODAL_ZOOM_STEP);
            }
        } else if (isModalOpen && e.key === '0') {
            if (getModalImageElement()) {
                e.preventDefault();
                resetModalZoom();
            }
        }
    });

    function escapeHtml(text) {
        if (text == null) return '';
        return String(text).replace(/[&<>"']/g, m => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[m]);
    }

    loadImages(true);
});
