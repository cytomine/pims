from PIL import Image
import os
import urllib.request
from fastapi import APIRouter
from pims.formats import FORMATS
import io
from pims.importer.importer import FileImporter
from tests.utils.formats import info_test, thumb_test, resized_test, mask_test, crop_test, crop_null_annot_test, histogram_perimage_test
import pytest
from pims.files.file import (
    EXTRACTED_DIR, HISTOGRAM_STEM, ORIGINAL_STEM, PROCESSED_DIR, Path,
    SPATIAL_STEM, UPLOAD_DIR_PREFIX
)
from pims.formats.utils.factories import FormatFactory
from pims.api.utils.models import HistogramType
from pims.processing.histograms.utils import build_histogram_file
import pytest, subprocess

def get_image(path, filename, root):
    filepath = os.path.join(path, filename)
    # If image does not exist locally -> download image
    filepath = os.path.join(path, filename)
    # If image does not exist locally -> download image
    
    if not os.path.exists("/tmp/images"):
        os.mkdir("/tmp/images")

    if not os.path.exists(root):
        os.mkdir(root)
	 
    if not os.path.exists(f"/tmp/images/{filename}"):
        try:
            url = f"https://data.cytomine.coop/open/tests/test%202.zip"
            urllib.request.urlretrieve(url, f"/tmp/images/{filename}")
        except Exception as e:
            print("Could not download image")
            print(e)
    
    if not os.path.exists(filepath): 
        #os.mkdir(path)
        image_path = f"/tmp/images/{filename}"
        pims_root = root
        #print(root)
        importer_path = f"/app/pims/importer/import_local_images.py" # pims folder should be in root folder
        import_img=subprocess.run(["python3", importer_path, "--path", image_path], stdout=subprocess.PIPE)
        
        subdirs = os.listdir(pims_root)
        for subdir in subdirs:
            if "upload-" in str(subdir):
                subsubdirs = os.listdir(os.path.join(root, subdir))
                for subsubdir in subsubdirs:
                    if filename in str(subsubdir):
                        upload_dir = os.path.join(root, str(subdir))
                        break
        print(upload_dir)
        if os.path.exists(path):
            os.unlink(path) # if the folder upload_test_bioformats_czi already exists the symlink won't work
        os.symlink(upload_dir, path)
			
def test_exists(image_path_excentric_filename1, root):
    path, filename = image_path_excentric_filename1
    get_image(path, filename, root)
    assert os.path.exists(os.path.join(path, filename)) == True
	
def test_info(client, image_path_excentric_filename1):
    _, filename = image_path_excentric_filename1
    response = client.get(f'/image/upload_test_excentric1/{filename}/info')
    assert response.status_code == 200
    assert "mrxs" in response.json()['image']['original_format'].lower()
    
    assert response.json()['image']['width'] == 109240
    assert response.json()['image']['height'] == 220696
	
def test_norm_tile(client, image_path_excentric_filename1):
    path, filename = image_path_excentric_filename1
    response = client.get(f"/image/upload_test_excentric1/{filename}/normalized-tile/zoom/1/ti/0", headers={"accept": "image/jpeg"})
    assert response.status_code == 200
	
    img_response = Image.open(io.BytesIO(response.content))
    width_resp, height_resp = img_response.size

    assert width_resp == 214
    assert height_resp == 256
	
def test_thumb(client, image_path_excentric_filename1):
    _, filename = image_path_excentric_filename1
    thumb_test(client, filename, "excentric1")
		
def test_resized(client, image_path_excentric_filename1):
    _, filename = image_path_excentric_filename1
    resized_test(client, filename, "excentric1")
	    
def test_mask(client, image_path_excentric_filename1):
    _, filename = image_path_excentric_filename1
    mask_test(client, filename, "excentric1")
	
def test_crop(client, image_path_excentric_filename1):
    _, filename = image_path_excentric_filename1
    crop_test(client, filename, "excentric1")

@pytest.mark.skip(reason="Does not return the correct response code")
def test_crop_null_annot(client, image_path_excentric_filename1):
    _, filename = image_path_excentric_filename1
    crop_null_annot_test(client, filename, "excentric1")
	
def test_histogram_perimage(client, image_path_excentric_filename1):
	_, filename = image_path_excentric_filename1
	histogram_perimage_test(client, filename, "excentric1")
